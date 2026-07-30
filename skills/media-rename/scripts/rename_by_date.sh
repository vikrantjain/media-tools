#!/usr/bin/env bash
# Rename media files to their capture timestamp: yyyymmdd_hhmmss.ext
# and set the filesystem modify-time to the same timestamp.
# Embedded metadata is NOT modified (only FileName + FileModifyDate change).
#
# Usage:
#   rename_by_date.sh preview FILE|DIR [FILE|DIR ...]   # dry run, shows planned renames
#   rename_by_date.sh apply   FILE|DIR [FILE|DIR ...]   # performs the renames
#
# Timestamp source, highest priority first:
#   CreationDate (local-time video tag) -> CreateDate -> DateTimeOriginal
# Files with none of these are left untouched and reported.
# Same-second collisions get -1, -2, ... suffixes.
#
# -api QuickTimeUTC=1 is required for correctness, not tidiness: QuickTime stores
# CreateDate in UTC. A non-Apple MP4 has no CreationDate, so without this flag it
# falls through to that UTC CreateDate and gets named with UTC wall-clock while
# every photo alongside it is named in local time -- a folder silently skewed by
# your UTC offset (verified: an 18:00Z clip was named 20200601_180000 instead of
# 20200601_233000 in +05:30). Apple video is unaffected: its CreationDate already
# carries an offset and still wins.
#
# Naming is allocated per Live Photo GROUP rather than per file -- see the header of
# plan_renames.py. exiftool's own date-based rename cannot do this: it names each file
# from that file's own timestamp with per-name collision counters, which split pairs
# whose still and clip straddle a second boundary and could give an unrelated still
# and video the same stem. Grouping needs a cross-file view, so exiftool is used to
# READ metadata and the rename itself is a plain os.rename/os.utime.
set -euo pipefail

MODE="${1:-}"
shift || true
if [ "$MODE" != "preview" ] && [ "$MODE" != "apply" ]; then
  echo "Usage: rename_by_date.sh preview|apply FILE|DIR ..." >&2
  exit 2
fi
if [ "$#" -eq 0 ]; then
  echo "ERROR: no files or directories given." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ET="$("$SCRIPT_DIR/../../media-metadata/scripts/ensure_exiftool.sh")"

exec python3 "$SCRIPT_DIR/plan_renames.py" "$MODE" "$ET" "$@"
