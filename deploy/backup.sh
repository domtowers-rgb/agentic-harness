#!/usr/bin/env bash
# Back up what isn't in git: settings (.env), SOUL.md, reminders, each
# chat's memory notes, and agentic-gateway's history (with its summaries
# and notes on people) and chat settings. One .tar.gz per run, readable
# by this account only; the newest KEEP are kept. Run nightly by
# deploy/agentic-backup.timer - see "Backups" in the README.
set -euo pipefail

HARNESS_DIR="${HARNESS_DIR:-$HOME/agentic-harness}"
GATEWAY_DIR="${GATEWAY_DIR:-$HOME/agentic-gateway}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/agentic-backups}"
KEEP="${KEEP:-7}"

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

files=()
for f in "$HARNESS_DIR/.env" "$HARNESS_DIR/SOUL.md" "$HARNESS_DIR/reminders.json" \
         "$GATEWAY_DIR/.env" "$GATEWAY_DIR/history.json" "$GATEWAY_DIR/chat_settings.json"; do
  [ -f "$f" ] && files+=("${f#/}")
done
if [ -d "$HARNESS_DIR/workspace" ]; then
  while IFS= read -r -d '' f; do files+=("${f#/}"); done < <(find "$HARNESS_DIR/workspace" -name memory.json -print0)
fi
if [ ${#files[@]} -eq 0 ]; then
  echo "nothing to back up" >&2
  exit 1
fi

out="$BACKUP_DIR/agentic-$(date +%Y-%m-%d_%H%M).tar.gz"
tar -czf "$out" -C / "${files[@]}"
ls -1t "$BACKUP_DIR"/agentic-*.tar.gz | tail -n +"$((KEEP + 1))" | xargs -r rm --
echo "backed up ${#files[@]} files to $out ($(du -h "$out" | cut -f1)); keeping the newest $KEEP"
