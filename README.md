# media-metadata

A Claude Code plugin for reading metadata from image and video files — **without needing `exiftool`**.

## What it does

Extracts EXIF, GPS, camera/lens info, timestamps, dimensions, and video codec/duration from
HEIC/HEIF, JPG, PNG, MOV, and MP4 files.

**Primary tool is exiftool** — the most complete option, and the only one here that reads GPS
embedded in `.MOV`/`.MP4`. The bootstrap prefers a system `exiftool` on PATH; otherwise it
downloads the standalone Perl distribution from CPAN **once** (~7 MB) and caches it under
`~/.cache/media-metadata` (pure Perl, needs only `perl`, no root, no compile). Subsequent runs
use the cache with no network. If exiftool is truly unavailable (no perl and no network), it
falls back to a `uv`-run `pillow-heif`/`pillow` + `ffprobe` path — fewer tags and no video
GPS, but fully offline.

Handles iPhone specifics: HEIC + Live Photo (`.MP4`) pairs, local-vs-UTC timestamp
correlation, and the PIL EXIF sub-IFD/GPS quirks that otherwise cost time to rediscover.

## How it works

The `media-metadata` skill activates when a task involves inspecting or filtering media by
embedded metadata. It runs a bundled script (`scripts/read_meta.py`) that takes a directory
or file list, writes a full `{filename: metadata}` JSON map, and prints a summary table
(date, device, GPS lat/lon, dimensions, duration). You then query the JSON for whatever the
task needs. For custom extraction, the skill documents the inline PIL/ffprobe recipe and its
gotchas.

## Usage

Ask naturally, e.g. "read the metadata of these images", "which of these photos have GPS",
"build a capture timeline for this folder". The skill is model-invoked.
