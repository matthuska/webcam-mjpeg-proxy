# Facecam MJPEG Loopback Proxy

This repository creates a WebEx-friendly virtual camera for an Elgato Facecam.
It exposes only the Facecam's MJPEG stream through `v4l2loopback`, avoiding the
raw USB video mode that can saturate a busy USB-C link.

The virtual camera stays visible using a low-FPS MJPEG placeholder stream. The
real Facecam stream starts only when an external application opens the proxy,
and stops again after a short idle timeout.

## Requirements

- Linux with V4L2
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

Copy the sample config and adjust `FACECAM_DEVICE` if auto-detection picks the
wrong camera:

```sh
cp facecam-loopback.env.example facecam-loopback.env
./scripts/probe-camera.sh
```

Create the loopback device:

```sh
sudo ./scripts/setup-loopback.sh --config ./facecam-loopback.env --replace
```

Run the relay in the foreground:

```sh
./scripts/relay.py --config ./facecam-loopback.env
```

Then start WebEx and select `Facecam MJPEG Proxy`.

## systemd Install

Install the system and user units:

```sh
./scripts/install-systemd.sh
```

Enable the root setup service:

```sh
sudo systemctl enable --now facecam-mjpeg-loopback-setup.service
```

Enable the user relay:

```sh
systemctl --user enable --now facecam-mjpeg-loopback-relay.service
```

Check logs:

```sh
journalctl -u facecam-mjpeg-loopback-setup.service
journalctl --user -u facecam-mjpeg-loopback-relay.service -f
```

Both services read the same config file:

```text
~/.config/facecam-mjpeg-loopback/facecam-loopback.env
```

The root setup service reads that file only for plain, allowlisted loopback
settings. It does not execute the config as shell code.

Older installs may have `/etc/facecam-mjpeg-loopback/facecam-loopback.env`.
Rerunning `./scripts/install-systemd.sh` removes that stale root copy.

## Configuration

The default config targets:

- Loopback device: `/dev/video6`
- Camera label: `Facecam MJPEG Proxy`
- Format: MJPEG
- Resolution: `1280x720`
- Frame rate: `30`
- Power-line frequency: `50 Hz`
- Zoom: `4`
- Placeholder frame rate: `1`
- Idle timeout: `5` seconds

Set `FACECAM_DEVICE` in `facecam-loopback.env` if auto-detection picks the
wrong camera. Prefer a stable path from `/dev/v4l/by-id/`.

`FACECAM_POWER_LINE_FREQUENCY=1` sets 50 Hz anti-flicker, which is the right
default for Germany. `FACECAM_ZOOM_ABSOLUTE=4` applies the default Facecam zoom.
Leave either value empty to skip setting that control.

Set `LOOPBACK_VIDEO_NR` and `LOOPBACK_DEVICE` to a low unused video number. If
WebEx does not show a high-numbered device such as `/dev/video42`, try
`/dev/video6` when your physical cameras occupy `/dev/video0` through
`/dev/video5`.

Run diagnostics with:

```sh
./scripts/doctor.sh --config ./facecam-loopback.env
```

The loopback should advertise `MJPG` at `1280x720`. If it shows an old format,
close WebEx, stop the relay, recreate the device, restart the relay, then start
WebEx again.

## How It Works

`v4l2loopback` creates a virtual V4L2 camera. The relay feeds it with a
low-FPS black MJPEG placeholder so WebEx can discover it. When another process
opens the proxy device, the relay switches to MJPEG packet copy from the
Facecam:

```sh
ffmpeg ... -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 \
  -i /dev/v4l/by-id/...Facecam... -c:v copy -f v4l2 /dev/video6
```

The important part is `-c:v copy`: `ffmpeg` does not decode MJPEG or convert to
raw video. It copies MJPEG packets from the Facecam into the loopback device,
and WebEx consumes the MJPEG stream from there.

Before starting the real camera producer, the relay applies the configured
Facecam controls with `v4l2-ctl`, currently power-line frequency and zoom.
