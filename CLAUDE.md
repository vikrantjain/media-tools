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
  **Use exiftool to read, never to write.** `media-rename` does its rename with
  `os.rename` + `os.utime`, which cannot reach the file's contents at all — prefer that
  over exiftool's `-FileName<`/`-FileModifyDate<` pseudo-tags in new code. Those two
  pseudo-tags are still safe if you need them (verified: md5 unchanged, no `_original`
  backups), but they put an exiftool write path next to irreplaceable files for no gain.
- Re-save an original through Pillow. `Image.open(p)` … `.save(p)` silently drops EXIF
  and re-encodes the pixels. Read originals; never write back to them.
- Write a derived file (contact sheet, thumbnail, report) to a path that might be an
  original. This was a real bug: `--sheet vacation.jpg` overwrote that photo in place
  and, because the name was excluded from the scan, never reported it. `cull.py` now
  stamps its sheets with `SHEET_MARKER` and refuses to overwrite any file lacking it —
  keep that guard on any new image-writing code path. Guard **every** path you will
  write before writing the first one: the per-page check used to let sheet page 1 land
  before page 2 was rejected.

## Shared code goes in `lib/media_common.py`

Quarantine moves, Live Photo pairing, collision suffixing, the undo command and the
sharpness metric are shared by `media-cull` and `media-dedup` and live in
`lib/media_common.py`. Do not copy them back into a skill. They were copies once, the
copies drifted, and both divergences cost data: dedup kept the unsafe
`mv … && rm -rf` undo (which overwrites a restored file and then deletes the evidence)
and silently suffixed `__1` without reporting it, months after cull had fixed both.

A fix to file-moving or overwrite behavior in one of these tools almost certainly
belongs in the shared module, so it reaches the other.

## The filename stem is the Live Photo contract between skills

`media-cull` and `media-dedup` decide that a video belongs to a still because they
**share a stem** — that is all they have, since neither reads EXIF. So any code that
renames must keep a pair on one stem, and must never give an unrelated still and video
the same stem. `media-rename` therefore groups on Apple's `ContentIdentifier`, not on
timestamps: the clip's `CreationDate` can be a second before the still's
`DateTimeOriginal`, and per-extension collision counters drift apart. Naming per file
split 5 of 37 pairs on a real export and forged 3 false ones, which would make a later
`--move` quarantine a stranger's video and orphan the real still.

If you touch naming, verify against `ContentIdentifier` rather than eyeballing stems:

```bash
exiftool -T -filename -ContentIdentifier *   # same id must => same stem, and vice versa
```

### Verifying a change respects this

Compare content hashes across the operation, not just the tags:

```bash
md5sum before.jpg              # then run the tool
md5sum renamed.jpg             # must match; renames/moves never alter bytes
exiftool -s -Make -Model -DateTimeOriginal renamed.jpg
ls -d *_original 2>/dev/null   # exiftool backups: there should be none
```
