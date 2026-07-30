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

**exiftool is required** here — it is the only thing that reads the correct **local**
capture time (including video, via `-api QuickTimeUTC=1`) and Apple's `ContentIdentifier`.
Use it via the shared bootstrap in the `media-metadata` skill. There is no good fallback for
this task (especially for video local time); if exiftool cannot be obtained, inform the user
and stop rather than improvising — see `../media-metadata/SKILL.md`.

exiftool reads the metadata; the script does the renaming itself. exiftool's native
date-based rename cannot be used, because it names each file from *that file's own*
timestamp with per-name collision counters, and both of those split Live Photo pairs — see
"Live Photo pairs" below.

## Always preview first, then apply

1. **Preview** (dry run — changes nothing) and show the user the planned renames:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/media-rename/scripts/rename_by_date.sh" \
     preview /path/to/media_dir
   ```

2. Look at the mapping. Call out anything notable: files with **no timestamp** (left
   unchanged), same-second **collisions** (auto-suffixed `-1`, `-2`, …), files pulled in
   uninvited to keep a pair together, and any Live Photo group that had two files of the
   same type. The preview reports each of these explicitly; `[pair]` marks paired files.

3. **Apply** only after the user is OK with the preview:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/media-rename/scripts/rename_by_date.sh" \
     apply /path/to/media_dir
   ```

Accepts individual files or directories (recurses into dirs with `-r`).

## Details / guarantees

- **Timestamp source**, **highest priority first**: `CreationDate` → `CreateDate` →
  `DateTimeOriginal`. For photos all present tags normally agree and this is capture time;
  for iPhone videos `CreationDate` carries **local** time with its UTC offset, so those get
  local wall-clock time. Within a Live Photo group the **still's** stamp names the whole
  group, since that is the shutter moment.
- **Video timezone**: QuickTime stores `CreateDate` in **UTC**, so the script passes
  `-api QuickTimeUTC=1` to convert it to local time. This matters for any video *without*
  `CreationDate` — a non-Apple/Android MP4 — which would otherwise be named with its UTC
  wall-clock while every photo beside it was named in local time, leaving the folder
  silently skewed by your UTC offset. Verified: an 18:00Z clip is named `20200601_233000`
  in `+05:30`, not `20200601_180000`. Apple video is unaffected either way, since its
  `CreationDate` already carries an offset and still wins.
- **Filesystem date**: the mtime is set to the same timestamp (this is the `touch`).
- **Embedded metadata untouched**: the rename is `os.rename` and the timestamp is `os.utime`
  — a directory entry and a filesystem attribute. Neither can rewrite a byte of the image,
  which is a stronger guarantee than exiftool's `-FileName<` offers, since no exiftool write
  path is involved at all. Verified byte-for-byte via md5, with no `_original` backups.
- **No timestamp?** Files lacking all three date tags are skipped and listed — they are not
  renamed. Decide with the user how to handle those (e.g. fall back to `FileModifyDate`).
  `media-cull`'s contact sheet has no date tags, so it is skipped automatically.
- **Live Photos: naming is per group, not per file.** A group is a still plus its motion clip,
  identified by Apple's **`ContentIdentifier`** — not by filename and not by timestamp. Every
  member gets one shared stem and keeps its own extension. Two independent bugs made the
  naive per-file approach split pairs, and both are why grouping is not optional:
  - **The still and the clip do not always share a timestamp.** iPhone writes the clip's
    `CreationDate` up to a second *before* the still's `DateTimeOriginal`
    (`IMG_1626.HEIC` 20:14:48 vs `IMG_1626.MP4` 20:14:47). Measured on a real 122-file
    export: **4 of 37 pairs** straddled a second boundary and landed on two stems.
  - **Collision counters were per target name**, so `.heic` and `.MP4` counted separately.
    Three stills in one second took ``/`-1`/`-2` while that second's single clip took ``:
    one pair split, and the clip then shared a stem with an *unrelated* still. Three such
    false pairs appeared in that same folder.

  This matters well beyond tidiness: `media-cull` and `media-dedup` pair a clip to its still
  **by stem**, and the recommended order is rename → cull → dedup. A forged stem makes a
  later `--move` carry off a stranger's clip and orphan the real still. On the folder above
  the fix gives 37/37 pairs intact, 0 split, 0 forged (previously 5 split, 3 forged).
- **Naming a single file adopts its partner.** Renaming `IMG_1794.HEIC` alone would orphan
  `IMG_1794.MP4`, so same-directory siblings sharing its `ContentIdentifier` are pulled in
  and reported. Unrelated files are never touched.
- **Idempotent**: a group already sitting on a correct stem keeps it, so re-running is a
  no-op rather than a renumbering. Renames are staged through temp names, so a set of files
  already holding each other's target names swaps cleanly instead of clobbering.
