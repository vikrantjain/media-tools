#!/usr/bin/env python3
"""Read metadata from images (HEIC/JPG) and videos (MOV/MP4).

Usage (always via uv so pillow-heif is available):
    uv run --with pillow-heif --with pillow python3 read_meta.py OUT.json PATH [PATH ...]

PATH may be a directory (scanned non-recursively) or individual files.
Images -> full EXIF incl. GPS via PIL. Videos -> ffprobe format+streams.
Writes a {filename: {...}} JSON map to OUT.json and prints a summary table.
"""
import sys, os, json, subprocess
import pillow_heif
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

pillow_heif.register_heif_opener()

IMAGE_EXTS = ("heic", "heif", "jpg", "jpeg", "png", "tif", "tiff")
VIDEO_EXTS = ("mov", "mp4", "m4v", "avi", "mkv")


def _decode(v):
    if isinstance(v, bytes):
        try:
            return v.decode(errors="replace")
        except Exception:
            return str(v)
    return v


def decode_ifd(ifd, tagmap):
    out = {}
    for tid, val in ifd.items():
        out[tagmap.get(tid, tid)] = _decode(val)
    return out


def image_meta(path):
    try:
        im = Image.open(path)
        exif = im.getexif()
        d = {}
        for tid, val in exif.items():
            tag = TAGS.get(tid, tid)
            if tag in ("GPSInfo", "ExifOffset"):
                continue
            d[tag] = _decode(val)
        try:
            d.update(decode_ifd(exif.get_ifd(0x8769), TAGS))  # Exif sub-IFD
        except Exception:
            pass
        try:
            gps = exif.get_ifd(0x8825)
            if gps:
                d["GPSInfo"] = decode_ifd(gps, GPSTAGS)
        except Exception:
            pass
        d["_size"] = im.size
        d["_mode"] = im.mode
        d["_format"] = im.format
        return d
    except Exception as e:
        return {"_error": str(e)}


def video_meta(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        return json.loads(r.stdout)
    except Exception as e:
        return {"_error": str(e)}


def gather_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                fp = os.path.join(p, f)
                if os.path.isfile(fp):
                    files.append(fp)
        elif os.path.isfile(p):
            files.append(p)
    return files


def dms_to_dd(dms, ref):
    try:
        deg, mn, sec = [float(x) for x in dms]
        dd = deg + mn / 60 + sec / 3600
        return round(-dd if ref in ("S", "W") else dd, 6)
    except Exception:
        return None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    out_path, paths = sys.argv[1], sys.argv[2:]
    results = {}
    for path in gather_files(paths):
        name = os.path.basename(path)
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        if ext in IMAGE_EXTS:
            results[name] = {"kind": "image", "meta": image_meta(path)}
        elif ext in VIDEO_EXTS:
            results[name] = {"kind": "video", "meta": video_meta(path)}
        else:
            results[name] = {"kind": "other"}

    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)

    # summary table
    for name, info in results.items():
        m = info.get("meta", {})
        if info["kind"] == "image":
            dt = m.get("DateTimeOriginal") or m.get("DateTime")
            model = m.get("Model")
            gps = m.get("GPSInfo") or {}
            latlon = ""
            if gps:
                lat = dms_to_dd(gps.get("GPSLatitude", []), gps.get("GPSLatitudeRef", ""))
                lon = dms_to_dd(gps.get("GPSLongitude", []), gps.get("GPSLongitudeRef", ""))
                latlon = f"{lat},{lon}"
            print(f"{name:32s} IMG {str(m.get('_size')):14s} {str(dt):20s} {str(model):16s} {latlon}")
        elif info["kind"] == "video":
            fmt = m.get("format", {})
            tags = fmt.get("tags", {})
            vs = next((s for s in m.get("streams", []) if s.get("codec_type") == "video"), {})
            res = f"{vs.get('width')}x{vs.get('height')}"
            print(f"{name:32s} VID {res:14s} {str(tags.get('creation_time')):20s} dur={fmt.get('duration')}")
        else:
            print(f"{name:32s} OTHER")


if __name__ == "__main__":
    main()
