#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="./facecam-loopback.env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$CONFIG_FILE"
  set +a
fi

FACECAM_DEVICE="${FACECAM_DEVICE:-}"
FACECAM_DEVICE_GLOB="${FACECAM_DEVICE_GLOB:-/dev/v4l/by-id/*Elgato*Facecam*}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"

echo "V4L2 devices:"
v4l2-ctl --list-devices || true

if [[ -z "$FACECAM_DEVICE" ]]; then
  matches=()
  shopt -s nullglob
  for candidate in $FACECAM_DEVICE_GLOB; do
    [[ "$candidate" == *index0* || "$candidate" != *index* ]] && matches+=("$candidate")
  done
  shopt -u nullglob
  FACECAM_DEVICE="${matches[0]:-}"
fi

if [[ -z "$FACECAM_DEVICE" ]]; then
  echo
  echo "No Facecam device matched. Set FACECAM_DEVICE to a /dev/videoX or /dev/v4l/by-id path."
  exit 1
fi

echo
echo "Selected camera: $FACECAM_DEVICE"
echo
v4l2-ctl -d "$FACECAM_DEVICE" --list-formats-ext

echo
if v4l2-ctl -d "$FACECAM_DEVICE" --list-formats-ext | grep -A30 -E "MJPG|MJPEG" | grep -q "${WIDTH}x${HEIGHT}"; then
  echo "MJPEG ${WIDTH}x${HEIGHT} appears to be supported. Check the interval list above for ${FPS} fps."
else
  echo "MJPEG ${WIDTH}x${HEIGHT} was not found in the advertised modes." >&2
  exit 1
fi

