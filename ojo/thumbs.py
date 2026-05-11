import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future

from ojo import imaging
from ojo.config import options
from ojo.util import _bytes, ext, get_failed_image

POOL_SIZE = max(1, (os.cpu_count() or 2) - 1)


def _safe_thumbnail(filename, cached, width, height, kill_event):
    try:
        if kill_event.is_set():
            return filename, cached

        if os.path.exists(cached):
            return filename, cached

        if os.path.isfile(filename) and not imaging.is_image(filename):
            return filename, cached

        if os.path.isdir(filename):
            return imaging.folder_thumbnail(filename, cached, width, height, kill_event)
        else:
            return imaging.thumbnail(filename, cached, width, height)
    except Exception:
        logging.exception("Error creating thumb for %s", filename)
        return filename, get_failed_image() if os.path.isfile(filename) else None


class Thumbs:
    def __init__(self, ojo):
        self.ojo = ojo
        self.pool = None
        self.killed = False
        self.lock = threading.Lock()
        self.queue = []
        self.processing = set()

    @staticmethod
    def get_thumbs_cache_dir(height):
        return os.path.expanduser("~/.config/ojo/cache/%d" % height)

    @staticmethod
    def get_folderthumbs_cache_dir(height):
        return os.path.expanduser("~/.config/ojo/cache/folderthumbs_%d" % height)

    def reset_queues(self):
        with self.lock:
            self.queue = []

    def stop(self):
        self.killed = True
        with self.lock:
            self.queue = []
            self.kill_event.set()
            self.thumbs_event.set()
            if self.pool:
                logging.info("%s: Shutting down ThreadPoolExecutor...", self)
                self.pool.shutdown(wait=True)
                self.pool = None
                self.thread.join(timeout=5)
                logging.info("%s: Stopped", self)

    def init_pool(self):
        with self.lock:
            self.pool = ThreadPoolExecutor(max_workers=POOL_SIZE)

    def start(self, ojo):
        self.queue = []
        self.processing = set()
        self.pool = None
        self.kill_event = threading.Event()
        self.thumbs_event = threading.Event()

        def _thumbs_thread():
            start_time = time.time()
            while self.ojo.mode == "image" and time.time() - start_time < 2:
                if self.killed:
                    return
                time.sleep(0.1)

            self.init_pool()

            cache_dir = self.get_thumbs_cache_dir(options["thumb_height"])
            os.makedirs(cache_dir, exist_ok=True)

            logging.info("Starting thumbs thread")

            while not self.killed:
                self.thumbs_event.wait(timeout=0.5)
                self.thumbs_event.clear()
                if self.killed:
                    return

                while self.queue and not self.killed:
                    with self.lock:
                        if len(self.processing) >= POOL_SIZE:
                            break

                    while time.time() - self.ojo.last_action_time < 1 and self.ojo.mode == "image":
                        if self.killed:
                            return
                        time.sleep(0.2)

                    time.sleep(0.02)

                    try:
                        with self.lock:
                            if not self.queue:
                                break
                            img = self.queue.pop(0)
                        self.add_thumbnail(img)
                    except Exception:
                        logging.exception("Exception in thumbs thread:")

        from ojo.ojo import OjoThread

        self.thread = OjoThread(ojo=ojo, target=_thumbs_thread)
        if not self.killed:
            self.thread.start()

    def priority_thumbs(self, files):
        if self.killed:
            return
        with self.lock:
            pq = set(files)
            self.queue = files + [f for f in self.queue if f not in pq]
        self.thumbs_event.set()

    def enqueue(self, files):
        if self.killed:
            return
        with self.lock:
            existing = set(self.queue)
            self.queue.extend(f for f in files if f not in existing)
        self.thumbs_event.set()

    @staticmethod
    def get_cached_thumbnail_path(filename, force_cache=False, thumb_height=None):
        if not force_cache and ext(filename) == ".gif":
            return filename

        if thumb_height is None:
            thumb_height = options["thumb_height"]

        try:
            mtime = os.path.getmtime(filename)
        except OSError:
            mtime = 0
        hash_input = _bytes(filename + "{0:.2f}".format(mtime))
        h = hashlib.md5(hash_input).hexdigest()
        folder = os.path.dirname(filename)
        if folder.startswith(os.sep):
            folder = folder[1:]
        return os.path.join(
            Thumbs.get_thumbs_cache_dir(thumb_height),
            folder,
            os.path.basename(filename) + "_" + h + ".jpg",
        )

    @staticmethod
    def get_folder_thumbnail_path(folder):
        if not os.path.isdir(folder):
            raise Exception("Requested folder thumb for non-folder: " + folder)

        folder = os.path.abspath(folder)
        try:
            mtime = os.path.getmtime(folder)
        except OSError:
            mtime = 0
        h = hashlib.md5(_bytes(folder + "{0:.2f}".format(mtime))).hexdigest()

        parent = os.path.dirname(folder)
        if parent.startswith(os.sep):
            parent = parent[1:]

        return os.path.join(
            Thumbs.get_folderthumbs_cache_dir(options["thumb_height"]),
            parent,
            os.path.basename(folder) + "_" + h + ".png",
        )

    def on_thumb_ready(self, img, thumb_path):
        with self.lock:
            self.processing.discard(img)
        self.thumbs_event.set()
        if thumb_path:
            self.ojo.on_thumb_ready(img, thumb_path)

    def on_thumb_failed(self, img, thumb_path):
        with self.lock:
            self.processing.discard(img)
        self.thumbs_event.set()
        self.ojo.on_thumb_failed(img, thumb_path)

    def add_thumbnail(self, img):
        th = options["thumb_height"]
        self.prepare_thumbnail(img, 3 * th, th)

    def prepare_thumbnail(self, filename, width, height):
        with self.lock:
            self.processing.add(filename)

        is_folder = os.path.isdir(filename)
        cached = (
            self.get_folder_thumbnail_path(filename)
            if is_folder
            else self.get_cached_thumbnail_path(filename)
        )

        def _thumbnail_ready(future):
            try:
                filename, thumb_path = future.result()
            except Exception:
                logging.exception("Thumbnail future failed")
                with self.lock:
                    self.processing.discard(filename)
                self.thumbs_event.set()
                return

            if thumb_path is None:
                self.on_thumb_ready(filename, None)
            elif not os.path.isfile(thumb_path) or not os.path.getsize(thumb_path):
                self.on_thumb_failed(filename, "Could not create thumbnail")
            else:
                self.on_thumb_ready(filename, thumb_path)

        if self.killed or not self.pool:
            return

        try:
            future = self.pool.submit(_safe_thumbnail, filename, cached, width, height, self.kill_event)
            future.add_done_callback(_thumbnail_ready)
        except RuntimeError:
            with self.lock:
                self.processing.discard(filename)

    def clear_thumbnails(self, folder):
        for img in imaging.list_images(folder):
            if self.killed:
                return
            cached = self.get_cached_thumbnail_path(img, True)
            if os.path.isfile(cached) and cached.startswith(
                self.get_thumbs_cache_dir(options["thumb_height"]) + os.sep
            ):
                try:
                    os.unlink(cached)
                except OSError:
                    logging.exception("Could not delete %s" % cached)
