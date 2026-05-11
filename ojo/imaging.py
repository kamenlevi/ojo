# coding=utf-8
import base64
import io
import logging
import os
import random
import tempfile
import threading

import gi
from gi.repository import GdkPixbuf, Gio, GObject
from PIL import Image

from ojo import config
from ojo.exiftool import ExifTool
from ojo.metadata import metadata
from ojo.util import ext

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS

gi.require_version("GdkPixbuf", "2.0")

NON_RAW_FORMATS = {
    ".bmp", ".dib", ".dcx", ".eps", ".ps", ".gif", ".im",
    ".jpg", ".jpe", ".jpeg", ".pcd", ".pcx", ".png",
    ".pbm", ".pgm", ".ppm", ".psd", ".tif", ".tiff",
    ".xbm", ".xpm", ".webp", ".avif", ".heic", ".heif",
    ".jxl",
}

RAW_FORMATS = {
    ".3fr", ".ari", ".arw", ".bay", ".braw", ".crw", ".cr2", ".cr3",
    ".cap", ".data", ".dcs", ".dcr", ".dng", ".drf", ".eip", ".erf",
    ".fff", ".gpr", ".iiq", ".k25", ".kdc", ".mdc", ".mef", ".mos",
    ".mrw", ".nef", ".nrw", ".obm", ".orf", ".pef", ".ptx", ".pxn",
    ".r3d", ".raf", ".raw", ".rwl", ".rw2", ".rwz", ".sr2", ".srf",
    ".srw", ".tif", ".x3f",
}

_supported_extensions_cache = None

exiftool = None
_lock = threading.Lock()


def start_exiftool_process(show_version=False):
    logging.debug('Starting exiftool in process %d', os.getpid())
    global exiftool
    with _lock:
        exiftool = ExifTool(executable=config.get_exiftool_path())
        exiftool.start(show_version)


def stop_exiftool_process():
    global exiftool
    with _lock:
        if exiftool:
            exiftool.terminate()
            exiftool = None


def get_optimal_preview(filename, to_folder, width=None, height=None):
    exiftool.extract_previews(filename, to_folder)

    previews = [
        {"path": os.path.join(to_folder, name)}
        for name in os.listdir(to_folder)
        if name.endswith((".jpg", ".jpeg", ".png"))
    ]

    if not previews:
        raise Exception("No previews found for %s" % filename)

    for p in previews:
        w, h = get_size_via_pixbuf(p["path"])
        p["width"] = w
        p["height"] = h

    if width is None or height is None:
        preview = max(previews, key=lambda p: p["width"])
    else:
        bigger = [p for p in previews if p["width"] >= width and p["height"] >= height]
        if bigger:
            preview = min(bigger, key=lambda p: p["width"])
        else:
            preview = max(previews, key=lambda p: p["width"])

    return preview["path"]


def get_pil(filename, width=None, height=None, fallback_to_preview=False):
    meta = metadata.get(filename)
    orientation = meta["orientation"]

    try:
        pil_image = Image.open(filename)
        pil_image.load()
    except (IOError, OSError, Image.DecompressionBombError):
        if not fallback_to_preview:
            raise
        with tempfile.TemporaryDirectory(prefix="ojo") as to_folder:
            optimal_preview = get_optimal_preview(filename, to_folder, width, height)
            pil_image = Image.open(optimal_preview)
            pil_image.load()

    if width is not None:
        pil_image.thumbnail((max(width, height), max(width, height)), _LANCZOS)
        pil_image = auto_rotate_pil(orientation, pil_image)
        if pil_image.size[0] > width or pil_image.size[1] > height:
            pil_image.thumbnail((width, height), _LANCZOS)
    else:
        pil_image = auto_rotate_pil(orientation, pil_image)

    return pil_image


def get_pixbuf(filename, width=None, height=None):
    meta = metadata.get(filename)
    orientation = meta["orientation"]
    image_width, image_height = meta["width"], meta["height"]

    def _from_preview():
        try:
            with tempfile.TemporaryDirectory(prefix="ojo") as to_folder:
                optimal_preview = get_optimal_preview(filename, to_folder, width, height)
                pixbuf = pixbuf_from_file(optimal_preview)
            pixbuf = auto_rotate_pixbuf(orientation, pixbuf)
            return pixbuf
        except Exception:
            return None

    def _from_gdk_pixbuf():
        try:
            pixbuf = pixbuf_from_file(filename)
            pixbuf = auto_rotate_pixbuf(orientation, pixbuf)
            return pixbuf
        except Exception:
            return None

    def _from_pil():
        try:
            pil = get_pil(filename)
            pixbuf = pil_to_pixbuf(pil)
            pil.close()
            return pixbuf
        except Exception:
            return None

    if ext(filename) in RAW_FORMATS:
        pixbuf = _from_preview()
        if not pixbuf:
            pixbuf = _from_gdk_pixbuf()
    else:
        pixbuf = _from_gdk_pixbuf()
        if not pixbuf:
            pixbuf = _from_preview()

    if not pixbuf:
        pixbuf = _from_pil()

    if not pixbuf:
        raise Exception("Could not load %s" % filename)

    if width is not None and (width < image_width or height < image_height):
        ratio = float(image_width) / image_height
        if float(width) / height < ratio:
            new_w = width
            new_h = max(1, int(width / ratio))
        else:
            new_h = height
            new_w = max(1, int(height * ratio))
        pixbuf = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)

    return pixbuf


def thumbnail(filename, thumb_path, width, height):
    temp_fd, tmp_thumb_path = tempfile.mkstemp(prefix="ojo_thumbnail_")
    os.close(temp_fd)

    def use_pil():
        pil = get_pil(filename, width, height)
        try:
            pil.save(tmp_thumb_path, "JPEG", quality=85, optimize=True)
        finally:
            pil.close()

    def use_pixbuf():
        pixbuf = get_pixbuf(filename, width, height)
        pixbuf.savev(tmp_thumb_path, "jpeg", ["quality"], ["85"])

    cache_dir = os.path.dirname(thumb_path)
    os.makedirs(cache_dir, exist_ok=True)

    if ext(filename) in {".gif", ".png", ".svg", ".xpm"}.union(RAW_FORMATS):
        try:
            use_pixbuf()
        except Exception:
            use_pil()
    else:
        try:
            use_pil()
        except Exception:
            use_pixbuf()

    os.replace(tmp_thumb_path, thumb_path)
    return filename, thumb_path


def folder_thumb_height(thumb_height):
    return int(thumb_height / 4)


def folder_thumbnail(folder, thumb_path, width, height, kill_event):
    cache_dir = os.path.dirname(thumb_path)
    os.makedirs(cache_dir, exist_ok=True)

    images = list_images(folder)
    if not images:
        return folder, None

    from ojo.thumbs import Thumbs

    random.seed(1234)
    random.shuffle(images)

    MAX_WIDTH = 400
    MAX_IMAGES = 20
    THUMB_HEIGHT = folder_thumb_height(height)
    MARGIN = 8

    image = Image.new("RGBA", (MAX_WIDTH + 100, THUMB_HEIGHT))

    total_width = 0
    for f in images[:MAX_IMAGES]:
        if kill_event.is_set():
            return folder, None

        try:
            fthumb = Thumbs.get_cached_thumbnail_path(f, height)
            if not os.path.exists(fthumb):
                _, fthumb = thumbnail(f, fthumb, 3 * height, height)
            fthumb_image = get_pil(fthumb, MAX_WIDTH, THUMB_HEIGHT)
            w, h = fthumb_image.size
            if total_width + MARGIN + w > MAX_WIDTH + 100:
                fthumb_image.close()
                break
            image.paste(fthumb_image, (total_width, 0, total_width + w, h))
            fthumb_image.close()
            total_width += MARGIN + w
        except Exception:
            logging.exception("folder_thumbnail: Failed thumbing %s" % f)

    if total_width > 0:
        image = image.crop((0, 0, min(MAX_WIDTH, total_width), THUMB_HEIGHT))
        fd, tmp_thumb_path = tempfile.mkstemp(prefix="ojo_folder_thumbnail_")
        os.close(fd)
        image.save(tmp_thumb_path, "PNG")
        os.replace(tmp_thumb_path, thumb_path)

    return folder, thumb_path


def auto_rotate_pil(orientation, im):
    if orientation is None:
        return im
    elif orientation in (1, "Horizontal (normal)"):
        return im
    elif orientation in (2, "Mirror horizontal"):
        return im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif orientation in (3, "Rotate 180"):
        return im.transpose(Image.Transpose.ROTATE_180)
    elif orientation in (4, "Mirror vertical"):
        return im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    elif orientation in (5, "Mirror horizontal and rotate 270 CW"):
        return im.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.ROTATE_90)
    elif orientation in (6, "Rotate 90 CW"):
        return im.transpose(Image.Transpose.ROTATE_270)
    elif orientation in (7, "Mirror horizontal and rotate 90 CW"):
        return im.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.ROTATE_270)
    elif orientation in (8, "Rotate 270 CW"):
        return im.transpose(Image.Transpose.ROTATE_90)
    return im


def auto_rotate_pixbuf(orientation, im):
    try:
        orientation = int(im.get_options()["orientation"])
    except Exception:
        pass

    if orientation is None:
        return im
    elif orientation in (1, "Horizontal (normal)"):
        return im
    elif orientation in (2, "Mirror horizontal"):
        return im.flip(True)
    elif orientation in (3, "Rotate 180"):
        return im.rotate_simple(180)
    elif orientation in (4, "Mirror vertical"):
        return im.flip(False)
    elif orientation in (5, "Mirror horizontal and rotate 270 CW"):
        return im.flip(True).rotate_simple(90)
    elif orientation in (6, "Rotate 90 CW"):
        return im.rotate_simple(270)
    elif orientation in (7, "Mirror horizontal and rotate 90 CW"):
        return im.flip(True).rotate_simple(270)
    elif orientation in (8, "Rotate 270 CW"):
        return im.rotate_simple(90)
    return im


def pil_to_pixbuf(pil_image):
    if pil_image.mode not in ("RGB", "RGBA"):
        pil_image = pil_image.convert("RGB")
    has_alpha = pil_image.mode == "RGBA"
    w, h = pil_image.size
    data = pil_image.tobytes()
    return GdkPixbuf.Pixbuf.new_from_data(
        data, GdkPixbuf.Colorspace.RGB, has_alpha,
        8, w, h, w * (4 if has_alpha else 3),
    )


def pil_to_base64(pil_image):
    output = io.BytesIO()
    pil_image.save(output, "PNG")
    contents = base64.b64encode(output.getvalue()).decode("ascii")
    output.close()
    return contents


def pixbuf_from_data(data):
    input_str = Gio.MemoryInputStream.new_from_data(data, None)
    return GdkPixbuf.Pixbuf.new_from_stream(input_str, None)


def pixbuf_from_file(filename):
    return GdkPixbuf.Pixbuf.new_from_file(filename)


def pixbuf_to_b64(pixbuf):
    return base64.b64encode(pixbuf.save_to_bufferv("png", [], [])[1]).decode("ascii")


def get_supported_image_extensions():
    global _supported_extensions_cache
    if _supported_extensions_cache is None:
        _supported_extensions_cache = set(NON_RAW_FORMATS) | set(RAW_FORMATS)
        for fmt in GdkPixbuf.Pixbuf.get_formats():
            for e in fmt.get_extensions():
                _supported_extensions_cache.add('.' + e.lower())
    return _supported_extensions_cache


def get_size_via_pixbuf(image):
    fmt, image_width, image_height = GdkPixbuf.Pixbuf.get_file_info(image)
    if fmt:
        return image_width, image_height
    try:
        with Image.open(image) as im:
            return im.size
    except Exception:
        raise Exception("Not an image or unsupported image format")


def is_image(filename):
    try:
        return os.path.isfile(filename) and ext(filename) in get_supported_image_extensions()
    except Exception:
        return False


def list_images(folder):
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    full_paths = [os.path.join(folder, f) for f in entries]
    return [f for f in full_paths if is_image(f)]
