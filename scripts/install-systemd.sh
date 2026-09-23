#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user_app_dir="$HOME/.local/share/mjpeg-camera-loopback"
user_config_dir="$HOME/.config/mjpeg-camera-loopback"
user_config_file="$user_config_dir/mjpeg-camera-loopback.toml"
user_unit_dir="$HOME/.config/systemd/user"
system_app_dir="/etc/mjpeg-camera-loopback"
system_unit="/etc/systemd/system/mjpeg-camera-loopback-setup.service"
old_user_unit="$user_unit_dir/facecam-mjpeg-loopback-relay.service"
old_system_unit="/etc/systemd/system/facecam-mjpeg-loopback-setup.service"
source_config="$repo_root/mjpeg-camera-loopback.toml.example"

if [[ -f "$repo_root/mjpeg-camera-loopback.toml" ]]; then
  source_config="$repo_root/mjpeg-camera-loopback.toml"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this installer as root; run it as your normal desktop user." >&2
  exit 1
fi

mkdir -p "$user_app_dir/scripts" "$user_config_dir" "$user_unit_dir"
systemctl --user disable --now facecam-mjpeg-loopback-relay.service >/dev/null 2>&1 || true
rm -f "$old_user_unit"
cp "$repo_root/scripts/relay.py" "$user_app_dir/scripts/"
cp "$repo_root/scripts/mjpeg_config.py" "$user_app_dir/scripts/"
cp "$repo_root/scripts/setup-loopback.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/probe-camera.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/doctor.sh" "$user_app_dir/scripts/"
if [[ ! -f "$user_config_file" ]]; then
  cp "$source_config" "$user_config_file"
else
  echo "Keeping existing $user_config_file"
fi
cp "$repo_root/systemd/mjpeg-camera-loopback-relay.service" "$user_unit_dir/"
systemctl --user daemon-reload

echo "Installed user relay files under $user_app_dir"
echo "Installed shared config at $user_config_file"

echo
echo "Installing root setup service with sudo."
sudo systemctl disable --now facecam-mjpeg-loopback-setup.service >/dev/null 2>&1 || true
sudo rm -f "$old_system_unit"
sudo mkdir -p "$system_app_dir/scripts"
sudo cp "$repo_root/scripts/setup-loopback.sh" "$system_app_dir/scripts/"
sudo cp "$repo_root/scripts/mjpeg_config.py" "$system_app_dir/scripts/"
sudo rm -f "$system_app_dir/mjpeg-camera-loopback.toml"
sed \
  -e "s|@SYSTEM_APP_DIR@|$system_app_dir|g" \
  -e "s|@USER_CONFIG_FILE@|$user_config_file|g" \
  "$repo_root/systemd/mjpeg-camera-loopback-setup.service" | sudo tee "$system_unit" >/dev/null
sudo systemctl daemon-reload

echo
echo "Installed $system_unit"
echo "Root setup service reads shared config at $user_config_file"
echo "Removed stale root config at $system_app_dir/mjpeg-camera-loopback.toml if it existed"
echo "Disabled old facecam-mjpeg-loopback systemd units if they were installed"
echo "Next:"
echo "  sudo systemctl enable --now mjpeg-camera-loopback-setup.service"
echo "  systemctl --user enable --now mjpeg-camera-loopback-relay.service"
