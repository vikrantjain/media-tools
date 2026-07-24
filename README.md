# media-tools

A Claude Code plugin with utilities for working with image and video files
(HEIC/HEIF, JPG, PNG, MOV, MP4, and more).

## Skills

### `media-metadata` — read metadata

Extracts EXIF, GPS, camera/lens info, timestamps, dimensions, and video codec/duration.

**Prefers exiftool** — the most complete option, and the only one here that reads GPS embedded
in `.MOV`/`.MP4`. The bootstrap uses a system `exiftool` on PATH if present; otherwise it
downloads the standalone Perl distribution from CPAN **once** (~7 MB) and caches it under
`~/.cache/media-tools` (pure Perl, needs only `perl`, no root, no compile). Subsequent runs
use the cache with no network. If exiftool truly can't be obtained (no perl and no network), it
falls back — **only after telling you and getting approval** — to a `uv`-run
`pillow-heif`/`pillow` + `ffprobe` path (fewer tags, no video GPS, but fully offline).

Ask naturally: "read the metadata of these images", "which photos have GPS", "build a capture
timeline for this folder".

### `media-rename` — rename by capture timestamp

Renames files to `yyyymmdd_hhmmss.ext` (e.g. `20231202_203640.jpg`) and stamps the filesystem
modified-time to match — **without altering embedded metadata** (file bytes stay identical).
Uses exiftool's native date-based rename via the shared bootstrap: local-time source
(`DateTimeOriginal` → `CreateDate` → `CreationDate`), `-c` collision suffixes, and a
preview-then-apply flow. Live Photo pairs (`.HEIC` + `.MP4`/`.MOV`) keep a shared base name.

Ask naturally: "rename these photos by the date they were taken", "organize this folder by
timestamp".

## iPhone specifics

Handles HEIC + Live Photo (`.MP4`) pairs and local-vs-UTC timestamp correlation (image
`DateTimeOriginal` is local; ffprobe `creation_time` is UTC; video `CreationDate` carries the
local offset).
