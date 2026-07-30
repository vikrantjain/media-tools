---
name: media-metadata
description: >
  Read metadata (EXIF, GPS, camera/lens info, timestamps, dimensions, video codec/duration)
  from image and video files — HEIC/HEIF, JPG, PNG, MOV, MP4, and more. Use whenever a task
  needs to inspect, extract, compare, or filter media by its embedded metadata. Handles Apple
  iPhone HEIC + Live Photo (.MP4) pairs and video GPS.
---

# Media Metadata

Extract metadata from images and videos. **exiftool is the best tool** — it is the most
complete and reads things the fallback cannot (e.g. GPS embedded in `.MOV`/`.MP4`, hundreds of
maker tags, more formats).

## Rule: always try exiftool first; never fall back silently

1. **Always** run `ensure_exiftool.sh` first and use exiftool if it resolves. This includes
   the one-time CPAN download — a ~7 MB fetch is expected and fine, not a reason to fall back.
2. **Only if exiftool cannot be obtained** (no `exiftool` on PATH, no cached copy, and the
   download fails because there's no network or no `perl`) do you consider the fallback.
3. Before using the fallback, **stop and tell the user**: exiftool is the best option, it is
   unavailable here (state why — e.g. no network + no perl), and the PIL/ffprobe fallback is
   more limited (**no video GPS**, fewer tags). **Ask for approval** to proceed with it.
4. Do **not** run the fallback until the user approves. If they decline, report that exiftool
   is required and stop.

## Primary path — exiftool (use this first)

Get a usable exiftool binary. The bootstrap checks PATH, then a local cache, else downloads
the standalone Perl distribution from CPAN **once** (~8 MB) and caches it under
`~/.cache/media-tools` — pure Perl, needs only `perl`, no root, no compile. The tarball is
Perl source that then gets executed, so it is **verified against a pinned sha256** (published
in CPAN's `CHECKSUMS`) before extraction, and unpacked to a temp dir and moved into place so
an interrupted run cannot leave a half-extracted cache behind:

```bash
ET=$("${CLAUDE_PLUGIN_ROOT}/skills/media-metadata/scripts/ensure_exiftool.sh")
```

Then use it. Useful invocations:

```bash
# Everything, as JSON, for a whole folder (best for programmatic use):
"$ET" -json -n -G /path/to/media_dir/*     # -n = numeric (decimal GPS), -G = group names

# A focused summary table across a folder:
"$ET" -T -filename -DateTimeOriginal -Model -GPSPosition -ImageSize -Duration /path/*

# One file, human-readable:
"$ET" -a -G1 -s /path/to/IMG_0001.HEIC
```

If the download 404s, the pinned release was pruned — CPAN lists only recent versions. Check
<https://exiftool.org> for the current version and update `VER` **and** `SHA256` together at
the top of `ensure_exiftool.sh`; the error message says so too.

Notes:
- `-n` gives decimal GPS/altitude instead of `deg min sec` — easier to parse.
- `-json` output is a list of objects; pipe to `jq` or `python3` to filter/reshape.
- exiftool reads **video GPS** (`-GPSPosition`, `-GPSCoordinates`) that PIL/ffprobe miss, and
  gives `CreationDate` with the local UTC offset.
- Don't dump `-MakerNote`/binary blobs into replies; select the tags you need.

## Fallback path — PIL + ffprobe (offline, no exiftool)

Use **only after** `ensure_exiftool.sh` has failed *and* the user has approved the fallback
per the rule above. This path has **no video GPS** and fewer tags. System `python3` usually
lacks pip/pillow-heif, so run via `uv` (do **not** `pip install`):

```bash
uv run --with pillow-heif --with pillow python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/media-metadata/scripts/read_meta.py" \
  /tmp/out.json /path/to/media_dir
```

`read_meta.py` writes full JSON + prints a summary table (date, device, GPS, dims, duration).
The JSON is keyed by **full path**, not basename, so passing two folders that both contain
`IMG_0001.HEIC` reports both (the table disambiguates them as `d1/IMG_0001.HEIC`).
Its non-obvious gotchas, if you adapt it: in PIL, `exif.items()` returns `GPSInfo` (0x8825) and
`ExifOffset` (0x8769) as **ints, not dicts** — skip them, then read `exif.get_ifd(0x8769)`
(lens/exposure/ISO/sub-second) and `exif.get_ifd(0x8825)` (GPS). GPS is `(deg,min,sec)` + ref.

## iPhone specifics

- A `.HEIC` + same-stem `.MP4` pair is one **Live Photo** (still + short motion clip).
- Image `DateTimeOriginal` is **local time**; pair it with `OffsetTimeOriginal` (or use
  exiftool's `CreationDate`, which includes the offset). ffprobe's `creation_time` is **UTC**.
