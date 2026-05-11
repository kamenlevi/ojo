import logging
import os
import threading
from collections import OrderedDict
from datetime import datetime

from ojo.util import ext

from ojo import imaging

METADATA_CACHE_SIZE = 5000


def needs_rotation(meta):
    orientation = meta.get("Orientation", {"val": ""})["val"]
    return "otate 90" in orientation or "otate 270" in orientation


class Metadata:
    def __init__(self):
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def clear_cache(self):
        with self.lock:
            self.cache.clear()

    def get(self, filename):
        with self.lock:
            meta = self.cache.get(filename)
            if meta:
                self.cache.move_to_end(filename)
                return meta

        meta = self.read(filename)
        if not meta:
            meta = self.read_via_pixbuf(filename)

        with self.lock:
            self.cache[filename] = meta
            if len(self.cache) > METADATA_CACHE_SIZE:
                self.cache.popitem(last=False)
        return meta

    def get_cached(self, filename):
        with self.lock:
            return self.cache.get(filename, None)

    def read_via_pixbuf(self, filename):
        w, h = imaging.get_size_via_pixbuf(filename)
        try:
            stat = os.stat(filename)
        except OSError:
            return {
                "filename": os.path.basename(filename),
                "needs_rotation": False,
                "width": w, "height": h,
                "orientation": None,
                "file_date": 0, "file_size": 0,
                "exif": {},
            }
        return {
            "filename": os.path.basename(filename),
            "needs_rotation": False,
            "width": w, "height": h,
            "orientation": None,
            "file_date": stat.st_mtime,
            "file_size": stat.st_size,
            "exif": {},
        }

    def read(self, filename):
        try:
            if imaging.exiftool is None or not imaging.exiftool.running:
                return None

            meta = imaging.exiftool.get_metadata(filename)
            meta["SourceFile"] = {"desc": "Source File", "val": meta["SourceFile"]}

            needs_rot = needs_rotation(meta)
            stat = os.stat(filename)

            w_key = "ImageWidth" if not needs_rot else "ImageHeight"
            h_key = "ImageHeight" if not needs_rot else "ImageWidth"

            width = meta.get(w_key, {}).get("val", 0)
            height = meta.get(h_key, {}).get("val", 0)
            if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
                width, height = 0, 0

            result = {
                "filename": os.path.basename(filename),
                "needs_rotation": needs_rot,
                "width": int(width),
                "height": int(height),
                "orientation": meta.get("Orientation", {"val": None})["val"],
                "file_date": stat.st_mtime,
                "file_size": stat.st_size,
                "exif": meta,
            }

            if ext(filename) == ".svg" or result["width"] == 0 or result["height"] == 0:
                meta_fallback = self.read_via_pixbuf(filename)
                result["width"] = meta_fallback["width"]
                result["height"] = meta_fallback["height"]

            return result

        except Exception:
            logging.exception("Could not parse meta-info for %s" % filename)
            return None


metadata = Metadata()
