#!/bin/bash
# Usage: phones/restore.sh <list.txt>   puts removed system apps back (user apps: reinstall from Play Store)
grep -vE '^\s*(#|$)' "$1" | while read -r p; do
  echo "$p: $(adb shell cmd package install-existing "$p" 2>&1 </dev/null)"
done
