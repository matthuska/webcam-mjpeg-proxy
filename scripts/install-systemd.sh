#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user_app_dir="$HOME/.local/share/facecam-mjpeg-loopback"
user_config_dir="$HOME/.config/facecam-mjpeg-loopback"
user_unit_dir="$HOME/.config/systemd/user"
system_env_dir="/etc/facecam-mjpeg-loopback"
system_unit="/etc/systemd/system/facecam-mjpeg-loopback-setup.service"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this installer as root; run it as your normal desktop user." >&2
  exit 1
fi

mkdir -p "$user_app_dir/scripts" "$user_config_dir" "$user_unit_dir"
cp "$repo_root/scripts/relay.py" "$user_app_dir/scripts/"
cp "$repo_root/scripts/setup-loopback.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/probe-camera.sh" "$user_app_dir/scripts/"
cp "$repo_root/scripts/doctor.sh" "$user_app_dir/scripts/"
if [[ ! -f "$user_config_dir/facecam-loopback.env" ]]; then
  cp "$repo_root/facecam-loopback.env.example" "$user_config_dir/facecam-loopback.env"
fi
cp "$repo_root/systemd/facecam-mjpeg-loopback-relay.service" "$user_unit_dir/"
systemctl --user daemon-reload

echo "Installed user relay files under $user_app_dir"
echo "Installed user config at $user_config_dir/facecam-loopback.env"

echo
echo "Installing root setup service with sudo."
sudo mkdir -p "$system_env_dir" /etc/facecam-mjpeg-loopback/scripts
if [[ ! -f "$system_env_dir/facecam-loopback.env" ]]; then
  sudo cp "$user_config_dir/facecam-loopback.env" "$system_env_dir/facecam-loopback.env"
else
  echo "Keeping existing $system_env_dir/facecam-loopback.env"
fi
sudo cp "$repo_root/scripts/setup-loopback.sh" /etc/facecam-mjpeg-loopback/scripts/
sed \
  -e "s|%E/facecam-mjpeg-loopback|/etc/facecam-mjpeg-loopback|g" \
  "$repo_root/systemd/facecam-mjpeg-loopback-setup.service" | sudo tee "$system_unit" >/dev/null
sudo systemctl daemon-reload

echo
echo "Installed $system_unit"
echo "Next:"
echo "  sudo systemctl enable --now facecam-mjpeg-loopback-setup.service"
echo "  systemctl --user enable --now facecam-mjpeg-loopback-relay.service"
