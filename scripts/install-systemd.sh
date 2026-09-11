#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user_app_dir="$HOME/.local/share/facecam-mjpeg-loopback"
user_config_dir="$HOME/.config/facecam-mjpeg-loopback"
user_config_file="$user_config_dir/facecam-loopback.env"
user_unit_dir="$HOME/.config/systemd/user"
system_app_dir="/etc/facecam-mjpeg-loopback"
system_unit="/etc/systemd/system/facecam-mjpeg-loopback-setup.service"
source_config="$repo_root/facecam-loopback.env.example"

if [[ -f "$repo_root/facecam-loopback.env" ]]; then
  source_config="$repo_root/facecam-loopback.env"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this installer as root; run it as your normal desktop user." >&2
  exit 1
fi

mkdir -p "$user_app_dir/scripts" "$user_config_dir" "$user_unit_dir"
cp "$repo_root/scripts/relay.py" "$user_app_dir/scripts/"
cp "$repo_root/scripts/setup-loopback.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/probe-camera.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/doctor.sh" "$user_app_dir/scripts/"
if [[ ! -f "$user_config_file" ]]; then
  cp "$source_config" "$user_config_file"
else
  echo "Keeping existing $user_config_file"
fi
cp "$repo_root/systemd/facecam-mjpeg-loopback-relay.service" "$user_unit_dir/"
systemctl --user daemon-reload

echo "Installed user relay files under $user_app_dir"
echo "Installed shared config at $user_config_file"

echo
echo "Installing root setup service with sudo."
sudo mkdir -p "$system_app_dir/scripts"
sudo cp "$repo_root/scripts/setup-loopback.sh" "$system_app_dir/scripts/"
sudo rm -f "$system_app_dir/facecam-loopback.env"
sed \
  -e "s|@SYSTEM_APP_DIR@|$system_app_dir|g" \
  -e "s|@USER_CONFIG_FILE@|$user_config_file|g" \
  "$repo_root/systemd/facecam-mjpeg-loopback-setup.service" | sudo tee "$system_unit" >/dev/null
sudo systemctl daemon-reload

echo
echo "Installed $system_unit"
echo "Root setup service reads shared config at $user_config_file"
echo "Removed stale root config at $system_app_dir/facecam-loopback.env if it existed"
echo "Next:"
echo "  sudo systemctl enable --now facecam-mjpeg-loopback-setup.service"
echo "  systemctl --user enable --now facecam-mjpeg-loopback-relay.service"
