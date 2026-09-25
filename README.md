# MJPEG Camera Loopback Proxy

By default, Webex on Linux may choose a webcam's raw video stream instead of
its MJPEG stream when both are available. Depending on your hardware, this can
cause flickering or video dropouts because raw video uses much more USB
bandwidth.

This repository creates a Webex-friendly virtual camera for USB webcams that
advertise MJPEG, such as an Elgato Facecam or Logitech Brio. It exposes only
the selected camera's MJPEG stream through `v4l2loopback`, avoiding raw USB
video modes that can saturate a busy USB-C link.

The virtual camera stays visible using a low-FPS MJPEG placeholder stream. The
real hardware camera is selected only when an external application opens the
proxy, so you can suspend the laptop, move between workplaces, and let the
relay choose whichever configured camera is plugged in.

## Requirements

- Linux with V4L2
- Python 3.11 or newer, for stdlib TOML parsing
- `ffmpeg`
- `v4l2-ctl`
- `v4l2loopback` kernel module
- `systemd`, for the optional services

On Ubuntu, the usual packages are:

```sh
sudo apt install ffmpeg v4l-utils
```

If `modinfo v4l2loopback` fails, install `v4l2loopback-dkms` too. Some Ubuntu
kernels ship the module already; others need DKMS to build it locally.

## Quick Start

Copy the sample TOML config and edit camera profiles if needed:

```sh
cp mjpeg-camera-loopback.toml.example mjpeg-camera-loopback.toml
./scripts/probe-camera.sh --config ./mjpeg-camera-loopback.toml
```

Create the loopback device:

```sh
sudo ./scripts/setup-loopback.sh --config ./mjpeg-camera-loopback.toml --replace
```

Run the relay in the foreground:

```sh
./scripts/relay.py --config ./mjpeg-camera-loopback.toml
```

Then start Webex and select `MJPEG Camera Proxy`.

## systemd Install

Install the system and user units:

```sh
./scripts/install-systemd.sh
```

The installer disables the old `facecam-mjpeg-loopback-*` services if they were
installed, because the old relay would otherwise keep feeding the same loopback
device.

Enable the root setup service:

```sh
sudo systemctl enable --now mjpeg-camera-loopback-setup.service
```

Enable the user relay:

```sh
systemctl --user enable --now mjpeg-camera-loopback-relay.service
```

Check logs:

```sh
journalctl -u mjpeg-camera-loopback-setup.service
journalctl --user -u mjpeg-camera-loopback-relay.service -f
```

Both services read the same config file:

```text
~/.config/mjpeg-camera-loopback/mjpeg-camera-loopback.toml
```

The root setup service reads that file through the Python TOML parser and uses
only the loopback settings. It does not execute the config as shell code.

## Configuration

The default config targets:

- Loopback device: `/dev/video6`
- Camera label: `MJPEG Camera Proxy`
- Format: MJPEG
- Resolution: `1280x720`
- Frame rate: `30`
- Placeholder frame rate: `1`
- Idle timeout: `5` seconds
- Camera priority: `facecam`, then `brio`

Example:

```toml
[loopback]
device = "/dev/video6"
video_nr = 6
label = "MJPEG Camera Proxy"
exclusive_caps = true

[capture]
width = 1280
height = 720
fps = 30

[placeholder]
fps = 1
mjpeg_quality = 31

[relay]
idle_timeout_seconds = 5
poll_interval_seconds = 0.5

[selection]
order = ["facecam", "brio"]

[cameras.facecam]
glob = "/dev/v4l/by-id/*Elgato*Facecam*-video-index0"

[cameras.facecam.controls]
power_line_frequency = 1
zoom_absolute = 4

[cameras.brio]
glob = "/dev/v4l/by-id/*Logitech*BRIO*-video-index0"

[cameras.brio.controls]
power_line_frequency = 1
zoom_absolute = 129
```

Add another `[cameras.<name>]` table for each additional webcam, then include
the name in `selection.order`. Each camera can declare only the controls that
make sense for that hardware. Unsupported controls are logged and skipped.

Run diagnostics with:

```sh
./scripts/doctor.sh --config ./mjpeg-camera-loopback.toml
```

The loopback should advertise `MJPG` at `1280x720`. If it shows an old format,
close Webex, stop the relay, recreate the device, restart the relay, then start
Webex again.

## How It Works

`v4l2loopback` creates a virtual V4L2 camera. The relay feeds it with a
low-FPS black MJPEG placeholder so Webex can discover it. When another process
opens the proxy device, the relay scans the configured camera profiles in
`selection.order`, chooses the first currently connected device, applies that
profile's controls, then starts MJPEG packet copy:

```sh
ffmpeg ... -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 \
  -i /dev/v4l/by-id/...-video-index0 -c:v copy -f v4l2 /dev/video6
```

The important part is `-c:v copy`: `ffmpeg` does not decode MJPEG or convert to
raw video. It copies MJPEG packets from the hardware camera into the loopback
device, and Webex consumes the MJPEG stream from there.

When there are no consumers, the relay returns to the placeholder stream. If a
camera disappears after suspend or unplug, `ffmpeg` exits, the relay falls back
to the placeholder, and the next active consumer check reselects from the
currently connected cameras.
