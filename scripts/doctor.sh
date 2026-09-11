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

LOOPBACK_VIDEO_NR="${LOOPBACK_VIDEO_NR:-6}"
LOOPBACK_DEVICE="${LOOPBACK_DEVICE:-/dev/video${LOOPBACK_VIDEO_NR}}"
LOOPBACK_LABEL="${LOOPBACK_LABEL:-Facecam MJPEG Proxy}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FACECAM_DEVICE="${FACECAM_DEVICE:-}"
EXPECTED_FORMAT=MJPG
EXPECTED_FORMAT_PATTERN="MJPG|JPEG"

section() {
  printf '\n== %s ==\n' "$1"
}

section "User"
id
if id -nG | tr ' ' '\n' | grep -qx video; then
  echo "User is in the video group."
elif [[ -e "$LOOPBACK_DEVICE" && -r "$LOOPBACK_DEVICE" && -w "$LOOPBACK_DEVICE" ]]; then
  echo "User is not in the video group, but has read/write access to $LOOPBACK_DEVICE."
else
  echo "User is not in the video group. Desktop ACLs may still allow access, but group membership is safer."
fi

section "Device Nodes"
ls -l /dev/video* 2>/dev/null || echo "No /dev/video* nodes visible."

section "Loopback Sysfs"
if [[ -e "/sys/class/video4linux/video${LOOPBACK_VIDEO_NR}" ]]; then
  cat "/sys/class/video4linux/video${LOOPBACK_VIDEO_NR}/name"
  cat "/sys/class/video4linux/video${LOOPBACK_VIDEO_NR}/dev"
else
  echo "No sysfs entry for video${LOOPBACK_VIDEO_NR}."
fi

section "Loopback Info"
if [[ -e "$LOOPBACK_DEVICE" ]]; then
  v4l2-ctl -d "$LOOPBACK_DEVICE" --all || true
  v4l2-ctl -d "$LOOPBACK_DEVICE" --list-formats-ext || true
  if command -v getfacl >/dev/null 2>&1; then
    getfacl -p "$LOOPBACK_DEVICE" || true
  fi
else
  echo "$LOOPBACK_DEVICE does not exist."
fi

section "Relay Processes"
pgrep -af 'relay.py|ffmpeg .*v4l2' || echo "No relay producer process found."
producer_pid="$(pgrep -n -f 'ffmpeg .*v4l2' || true)"
if [[ -n "$producer_pid" ]]; then
  ps -o pid,pcpu,pmem,comm,args -p "$producer_pid"
fi

section "Loopback Consumers"
if [[ -e "$LOOPBACK_DEVICE" ]]; then
  device_realpath="$(readlink -f "$LOOPBACK_DEVICE")"
  found_consumer=0
  relay_pid="$(pgrep -n -f 'relay.py' || true)"
  for proc_dir in /proc/[0-9]*; do
    pid="${proc_dir##*/}"
    [[ "$pid" == "$relay_pid" || "$pid" == "$producer_pid" ]] && continue
    fd_dir="$proc_dir/fd"
    [[ -d "$fd_dir" ]] || continue
    for fd in "$fd_dir"/*; do
      [[ -e "$fd" ]] || continue
      if [[ "$(readlink -f "$fd" 2>/dev/null || true)" == "$device_realpath" ]]; then
        name="$(cat "$proc_dir/comm" 2>/dev/null || echo unknown)"
        echo "$pid/$name"
        found_consumer=1
        break
      fi
    done
  done
  if [[ "$found_consumer" -eq 0 ]]; then
    echo "No external process currently has $LOOPBACK_DEVICE open."
  fi
fi

section "Facecam"
if [[ -n "$FACECAM_DEVICE" ]]; then
  echo "Configured camera: $FACECAM_DEVICE"
  v4l2-ctl -d "$FACECAM_DEVICE" --info || true
  echo
  echo "Configured controls:"
  v4l2-ctl -d "$FACECAM_DEVICE" --get-ctrl=power_line_frequency || true
  v4l2-ctl -d "$FACECAM_DEVICE" --get-ctrl=zoom_absolute || true
else
  echo "FACECAM_DEVICE is not set."
fi

section "Suggestions"
if [[ "$LOOPBACK_VIDEO_NR" -gt 9 ]]; then
  echo "Try a low loopback number such as /dev/video6; some apps miss high-numbered nodes."
fi
if [[ -e "$LOOPBACK_DEVICE" ]]; then
  if v4l2-ctl -d "$LOOPBACK_DEVICE" --info 2>/dev/null | grep -q "Video Capture"; then
    echo "$LOOPBACK_DEVICE advertises Video Capture."
  else
    echo "$LOOPBACK_DEVICE may not currently advertise Video Capture. Make sure the relay placeholder is running."
  fi
  expected_size="${WIDTH}x${HEIGHT}"
  if ! v4l2-ctl -d "$LOOPBACK_DEVICE" --list-formats-ext 2>/dev/null | grep -q "$expected_size"; then
    echo "$LOOPBACK_DEVICE is not advertising $expected_size. Stop the relay, rerun setup with --replace, then restart the relay."
  fi
  if ! v4l2-ctl -d "$LOOPBACK_DEVICE" --list-formats-ext 2>/dev/null | grep -Eq "$EXPECTED_FORMAT_PATTERN"; then
    echo "$LOOPBACK_DEVICE is not advertising $EXPECTED_FORMAT. Current format may be unsuitable for WebEx."
  fi
fi
echo "Restart WebEx after recreating the loopback device; camera lists are often cached at app startup."
