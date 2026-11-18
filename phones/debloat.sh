#!/bin/bash
# Usage: phones/debloat.sh <list.txt>   removes each package for user 0 (undo: phones/restore.sh <list.txt>)
# Lists are built per model from UAD-ng ratings; never add packages UAD-ng marks Unsafe.
set -u
list="$1"; log="${list%.txt}-result.log"; : > "$log"
grep -vE '^\s*(#|$)' "$list" | while read -r p; do
  out=$(adb shell pm uninstall --user 0 "$p" 2>&1 </dev/null)
  echo "$p: $out" | tee -a "$log"
done
