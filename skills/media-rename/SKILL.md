---
name: media-rename
description: >
  Rename image/video files to their capture timestamp in yyyymmdd_hhmmss format (e.g.
  20231202_203640) and set the filesystem modified-time to match — without altering the
  files' embedded metadata. Use when asked to rename/organize photos or videos by the date
  they were taken. Works on HEIC/JPG/PNG/MOV/MP4 and more.
---

# Media Rename (by capture timestamp)

Rename files to `yyyymmdd_hhmmss.ext` (e.g. `20231202_203640.jpg` = 2 Dec 2023, 20:36:40) and
stamp the filesystem modified-time with the same moment. **Embedded metadata is never changed**
— only the filename and the filesystem date.

**exiftool is the best (and required) tool** here: it renames by date natively, sets the
filesystem date in the same pass, uses the correct **local** capture time (including video),
and does not rewrite file content. Use it via the shared bootstrap in the `media-metadata`
skill. There is no good fallback for this task (especially for video local time); if exiftool
cannot be obtained, inform the user and stop rather than improvising — see `../media-metadata/SKILL.md`.

## Always preview first, then apply

1. **Preview** (dry run — changes nothing) and show the user the planned renames:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/media-rename/scripts/rename_by_date.sh" \
     preview /path/to/media_dir
   ```

2. Look at the mapping. Call out anything notable: files with **no timestamp** (left
   unchanged), and same-second **collisions** (auto-suffixed `-1`, `-2`, … via `%-c`).

3. **Apply** only after the user is OK with the preview:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/media-rename/scripts/rename_by_date.sh" \
     apply /path/to/media_dir
   ```

Accepts individual files or directories (recurses into dirs with `-r`).

## Details / guarantees

- **Timestamp source**, priority order (last present wins): `DateTimeOriginal` → `CreateDate`
  → `CreationDate`. For photos this is capture time; for iPhone videos `CreationDate` carries
  **local** time with offset, so videos get their local wall-clock time, not UTC.
- **Filesystem date**: `-FileModifyDate` is set to the same timestamp (this is the `touch`).
- **Embedded metadata untouched**: only `FileName` and `FileModifyDate` (both filesystem-level
  pseudo-tags) are written; file bytes are byte-for-byte identical (verified via md5).
- **No timestamp?** Files lacking all three date tags are skipped with a warning — they are not
  renamed. Decide with the user how to handle those (e.g. fall back to `FileModifyDate`).
- **Live Photos**: a `.HEIC` and its paired `.MP4`/`.MOV` share the capture time, so they get
  the same `yyyymmdd_hhmmss` base name (different extensions) — the pairing is preserved.
