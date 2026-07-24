#!/usr/bin/env bash
# Rename media files to their capture timestamp: yyyymmdd_hhmmss.ext
# and set the filesystem modify-time to the same timestamp.
# Embedded metadata is NOT modified (only FileName + FileModifyDate change).
#
# Usage:
#   rename_by_date.sh preview FILE|DIR [FILE|DIR ...]   # dry run, shows planned renames
#   rename_by_date.sh apply   FILE|DIR [FILE|DIR ...]   # performs the renames
#
# Timestamp source, in priority order (last present wins in exiftool):
#   DateTimeOriginal -> CreateDate -> CreationDate (local-time video tag)
# Files with none of these are left untouched (exiftool warns).
# Same-second collisions get -1, -2, ... suffixes (%-c).
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

FMT="%Y%m%d_%H%M%S%%-c.%%e"

if [ "$MODE" = "preview" ]; then
  # -testname shows the mapping without changing anything.
  exec "$ET" \
    '-testname<DateTimeOriginal' '-testname<CreateDate' '-testname<CreationDate' \
    -d "$FMT" -r "$@"
else
  # Rename + stamp filesystem mtime. Embedded tags are not rewritten.
  exec "$ET" \
    '-FileName<DateTimeOriginal' '-FileName<CreateDate' '-FileName<CreationDate' \
    '-FileModifyDate<DateTimeOriginal' '-FileModifyDate<CreateDate' '-FileModifyDate<CreationDate' \
    -d "$FMT" -r "$@"
fi
