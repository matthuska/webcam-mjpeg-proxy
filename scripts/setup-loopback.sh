#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="./facecam-loopback.env"
REPLACE_DEVICE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --replace)
      REPLACE_DEVICE=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

read_config_value() {
  local key="$1"
  local value="$2"
  value="$(trim "$value")"
  value="${value%%#*}"
  value="$(trim "$value")"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi

  case "$key" in
    LOOPBACK_VIDEO_NR) LOOPBACK_VIDEO_NR="$value" ;;
    LOOPBACK_DEVICE) LOOPBACK_DEVICE="$value" ;;
    LOOPBACK_LABEL) LOOPBACK_LABEL="$value" ;;
    LOOPBACK_EXCLUSIVE_CAPS) LOOPBACK_EXCLUSIVE_CAPS="$value" ;;
    WIDTH) WIDTH="$value" ;;
    HEIGHT) HEIGHT="$value" ;;
    FPS) FPS="$value" ;;
  esac
}

if [[ -f "$CONFIG_FILE" ]]; then
  while IFS= read -r line; do
    line="$(trim "$line")"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" == export[[:space:]]* ]]; then
      line="$(trim "${line#export}")"
    fi
    [[ "$line" == *=* ]] || continue
    key="$(trim "${line%%=*}")"
    value="${line#*=}"
    read_config_value "$key" "$value"
  done < "$CONFIG_FILE"
fi

LOOPBACK_VIDEO_NR="${LOOPBACK_VIDEO_NR:-6}"
LOOPBACK_DEVICE="${LOOPBACK_DEVICE:-/dev/video${LOOPBACK_VIDEO_NR}}"
LOOPBACK_LABEL="${LOOPBACK_LABEL:-Facecam MJPEG Proxy}"
LOOPBACK_EXCLUSIVE_CAPS="${LOOPBACK_EXCLUSIVE_CAPS:-1}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "setup-loopback.sh must run as root because it loads a kernel module." >&2
  exit 1
fi

if [[ "$REPLACE_DEVICE" -eq 1 ]]; then
  modprobe -r v4l2loopback 2>/dev/null || {
    echo "Could not unload v4l2loopback. Stop relay/WebEx clients and try again." >&2
    exit 1
  }
fi

if [[ -e "$LOOPBACK_DEVICE" ]]; then
  existing_name="$(v4l2-ctl -d "$LOOPBACK_DEVICE" --info 2>/dev/null | awk -F ': ' '/Card type/ {print $2; exit}')"
  if [[ "$existing_name" == "$LOOPBACK_LABEL" ]]; then
    echo "$LOOPBACK_DEVICE already exists with label '$LOOPBACK_LABEL'."
  else
    echo "$LOOPBACK_DEVICE already exists and does not look like this proxy." >&2
    echo "Found label: ${existing_name:-unknown}" >&2
    exit 1
  fi
else
  modprobe v4l2loopback \
    devices=1 \
    video_nr="$LOOPBACK_VIDEO_NR" \
    card_label="$LOOPBACK_LABEL" \
    exclusive_caps="$LOOPBACK_EXCLUSIVE_CAPS"
fi

if [[ ! -e "$LOOPBACK_DEVICE" ]]; then
  echo "Expected loopback device $LOOPBACK_DEVICE was not created." >&2
  exit 1
fi

echo "The relay will negotiate MJPEG ${WIDTH}x${HEIGHT}@${FPS} when it starts."

v4l2-ctl -d "$LOOPBACK_DEVICE" -c sustain_framerate=1 >/dev/null 2>&1 || true
v4l2-ctl -d "$LOOPBACK_DEVICE" -c timeout=3000 >/dev/null 2>&1 || true

echo "Loopback camera ready: $LOOPBACK_DEVICE ($LOOPBACK_LABEL)"
