# media-tools

## Never modify the data inside a photo

The metadata embedded in an image — EXIF, GPS, camera/lens, capture timestamps — is
**read-only, always**, whether by code in this plugin or by an agent acting directly.
Users bring irreplaceable originals to these tools; a rewritten tag cannot be undone.

Originals may be touched in exactly two ways, both of which leave the file's bytes
identical:

- **renamed** (`media-rename`)
- **moved** (`--move` quarantines in `media-cull` / `media-dedup`)

Changing the *filesystem* modify-time is allowed and is not covered by this rule —
it lives outside the file and rewrites none of the image's bytes. `media-rename`
sets it deliberately so file browsers sort by real capture time.

### Concretely, do not

- Pass exiftool any tag assignment (`-Tag=value`, `-overwrite_original`, `-tagsFromFile`).
  Only the filesystem pseudo-tags `-FileName<` and `-FileModifyDate<` are permitted;
  those rename/re-stamp without rewriting file contents (verified: md5 unchanged, no
  `_original` backups created).
- Re-save an original through Pillow. `Image.open(p)` … `.save(p)` silently drops EXIF
  and re-encodes the pixels. Read originals; never write back to them.
- Write a derived file (contact sheet, thumbnail, report) to a path that might be an
  original. This was a real bug: `--sheet vacation.jpg` overwrote that photo in place
  and, because the name was excluded from the scan, never reported it. `cull.py` now
  stamps its sheets with `SHEET_MARKER` and refuses to overwrite any file lacking it —
  keep that guard on any new image-writing code path.

### Verifying a change respects this

Compare content hashes across the operation, not just the tags:

```bash
md5sum before.jpg              # then run the tool
md5sum renamed.jpg             # must match; renames/moves never alter bytes
exiftool -s -Make -Model -DateTimeOriginal renamed.jpg
ls -d *_original 2>/dev/null   # exiftool backups: there should be none
```
