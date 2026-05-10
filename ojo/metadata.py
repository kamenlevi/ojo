import logging
import os
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

    def clear_cache(self):
        self.cache.clear()

    def get(self, filename):
        meta = self.cache.get(filename)
        if meta:
            self.cache.move_to_end(filename)
            return meta

        meta = self.read(filename)
        if not meta:
            meta = self.read_via_pixbuf(filename)

        self.cache[filename] = meta
        if len(self.cache) > METADATA_CACHE_SIZE:
            self.cache.popitem(last=False)
        return meta

    def get_cached(self, filename):
        return self.cache.get(filename, None)

    def read_via_pixbuf(self, filename):
        w, h = imaging.get_size_via_pixbuf(filename)
        stat = os.stat(filename)
        return {
            "filename": os.path.basename(filename),
            "needs_rotation": False,
            "width": w,
            "height": h,
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

            # also cache the most important part
            needs_rot = needs_rotation(meta)
            stat = os.stat(filename)

            result = {
                "filename": os.path.basename(filename),
                "needs_rotation": needs_rot,
                "width": meta["ImageWidth" if not needs_rot else "ImageHeight"]["val"],
                "height": meta["ImageHeight" if not needs_rot else "ImageWidth"]["val"],
                "orientation": meta.get("Orientation", {"val": None})["val"],
                "file_date": stat.st_mtime,
                "file_size": stat.st_size,
                "exif": meta,
            }

            if ext(filename) == ".svg":
                # svg sizing is special, exiftool could return things like "270mm" which causes
                # exceptions downstream, as width and height are expected to be numbers.
                # So use size from pixbuf, it works OK for svgs.
                meta_svg = self.read_via_pixbuf(filename)
                result["width"] = meta_svg["width"]
                result["height"] = meta_svg["height"]

            return result

        except Exception:
            logging.exception("Could not parse meta-info for %s" % filename)
            return None


metadata = Metadata()
