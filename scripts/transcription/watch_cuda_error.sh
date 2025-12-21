#!/usr/bin/env bash
# watch_cuda_error.sh — watch a logfile for a specific CUDA error and alert visibly
# Usage: ./watch_cuda_error.sh /path/to/logfile
# Optional env vars:
#   POPUP=1                # also show a modal popup (kdialog/zenity/xmessage if available)
#   SOUND=1                # play a short alert (canberra-gtk-play/paplay/beep/terminal-bell)
#   DEBOUNCE_SECONDS=10    # minimum seconds between alerts (default: 10)

set -Eeuo pipefail

ERRSTR="CUDA failed with error an illegal instruction was encountered"

log() { printf '[%(%F %T)T] %s\n' -1 "$*"; }

need() {
  command -v "$1" &>/dev/null
}

notify() {
  local title="$1" body="$2"
  # Prefer libnotify (notify-send) with critical urgency
  if need notify-send; then
    notify-send --urgency=critical "$title" "$body"
    return 0
  fi

  # Fallbacks: kdialog, zenity, xmessage (non-blocking)
  if [[ "${POPUP:-0}" == "1" ]]; then
    if need kdialog; then
      kdialog --error "$body" --title "$title" & disown || true
      return 0
    elif need zenity; then
      zenity --error --title="$title" --text="$body" & disown || true
      return 0
    elif need xmessage; then
      xmessage -center -buttons Okay:0 -title "$title" "$body" & disown || true
      return 0
    fi
  fi

  # Last resort: write loud line to TTY
  echo -e "\n*** $title ***\n$body\n"
}

sound() {
  [[ "${SOUND:-0}" == "1" ]] || return 0
  if need canberra-gtk-play; then
    canberra-gtk-play -i dialog-warning &>/dev/null || true
  elif need paplay && [[ -f /usr/share/sounds/freedesktop/stereo/dialog-warning.oga ]]; then
    paplay /usr/share/sounds/freedesktop/stereo/dialog-warning.oga &>/dev/null || true
  elif need beep; then
    beep -f 880 -l 120 -r 2 || true
  else
    printf '\a'  # terminal bell
  fi
}

show_alert() {
  local msg="Detected CUDA error in $(basename "$LOGFILE"):\n\n$ERRSTR\n\nTime: $(date '+%F %T')"
  notify "CUDA ERROR DETECTED" "$msg"
  sound
}

[[ $# -ge 1 ]] || { echo "Usage: $0 /path/to/logfile"; exit 2; }
LOGFILE="$1"
[[ -e "$LOGFILE" ]] || { echo "File not found: $LOGFILE"; exit 1; }

# Defaults
DEBOUNCE_SECONDS="${DEBOUNCE_SECONDS:-10}"
LAST_ALERT_EPOCH=0

# Ensure tail is line-buffered and resilient (-F follows rename/rotate)
log "Watching: $LOGFILE"
log "Error match: \"$ERRSTR\""
[[ "${POPUP:-0}" == "1" ]] && log "Popup: enabled"
[[ "${SOUND:-0}" == "1" ]] && log "Sound: enabled"
log "Debounce: ${DEBOUNCE_SECONDS}s"

# If grep supports --line-buffered use it, otherwise use awk as matcher
if grep --help 2>/dev/null | grep -q -- '--line-buffered'; then
  tail -n0 -F -- "$LOGFILE" \
    | stdbuf -oL grep --line-buffered -F "$ERRSTR" \
    | while IFS= read -r _line; do
        now=$(date +%s)
        if (( now - LAST_ALERT_EPOCH >= DEBOUNCE_SECONDS )); then
          LAST_ALERT_EPOCH="$now"
          show_alert
        fi
      done
else
  tail -n0 -F -- "$LOGFILE" \
    | stdbuf -oL awk -v pat="$ERRSTR" '
        index($0, pat) { print; fflush(); }' \
    | while IFS= read -r _line; do
        now=$(date +%s)
        if (( now - LAST_ALERT_EPOCH >= DEBOUNCE_SECONDS )); then
          LAST_ALERT_EPOCH="$now"
          show_alert
        fi
      done
fi
