# Facecam MJPEG Loopback Proxy

This repository creates a WebEx-friendly virtual camera for an Elgato Facecam.
The physical camera is opened as MJPEG at 720p30, then decoded into a
`v4l2loopback` device that video-call software can read.

The virtual camera stays visible using a low-FPS placeholder stream. The real
Facecam stream starts only when an external application opens the loopback
device, and stops again after a short idle timeout.

## Requirements

- Linux with V4L2
- `ffmpeg`
- `v4l2-ctl`
- `v4l2loopback-ctl`
- `v4l2loopback` kernel module
- `systemd`, for the optional services

On Ubuntu, the usual packages are:

```sh
sudo apt install ffmpeg v4l-utils v4l2loopback-utils
```

If `modinfo v4l2loopback` fails, install `v4l2loopback-dkms` too. Some Ubuntu
kernels ship the module already; others need DKMS to build it locally.

## Quick Start

Copy the sample config and adjust it if needed:

```sh
cp facecam-loopback.env.example facecam-loopback.env
```

Probe the camera:

```sh
./scripts/probe-camera.sh
```

Create the loopback device:

```sh
sudo ./scripts/setup-loopback.sh
```

Run the relay in the foreground:

```sh
./scripts/relay.py --config ./facecam-loopback.env
```

Then select `Facecam MJPEG Proxy` in WebEx or another video-call app.

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

## Configuration

The default config targets:

- Loopback device: `/dev/video6`
- Camera label: `Facecam MJPEG Proxy`
- Input format: MJPEG
- Resolution: `1280x720`
- Frame rate: `30`
- Loopback raw output format: `YUY2` / ffmpeg `yuyv422`
- Idle timeout: `5` seconds

Set `FACECAM_DEVICE` in `facecam-loopback.env` if auto-detection picks the
wrong camera. Prefer a stable path from `/dev/v4l/by-id/`.

Set `LOOPBACK_VIDEO_NR` and `LOOPBACK_DEVICE` to a low unused video number. If
WebEx does not show a high-numbered device such as `/dev/video42`, try
`/dev/video6` when your physical cameras occupy `/dev/video0` through
`/dev/video5`.

Run diagnostics with:

```sh
./scripts/doctor.sh --config ./facecam-loopback.env
```

If WebEx still does not list the proxy, close WebEx, stop the relay, recreate
the loopback device with a low number, restart the relay, then start WebEx:

```sh
sudo ./scripts/setup-loopback.sh --config ./facecam-loopback.env --replace
./scripts/relay.py --config ./facecam-loopback.env
```

The loopback should advertise `YUYV`/`YUY2` at `1280x720`. If it shows
`640x480 BGR4`, the old format is still locked; stop the relay before running
setup with `--replace`.

When `LOOPBACK_EXCLUSIVE_CAPS=1`, setup intentionally skips
`v4l2loopback-ctl set-caps`; the relay's placeholder producer establishes the
actual `1280x720` format. Start the relay before checking formats or launching
WebEx.

## How It Works

`v4l2loopback` creates a virtual V4L2 camera. The relay continuously feeds it
with a lightweight placeholder so WebEx can discover it. When another process
opens the proxy device, the relay switches to:

```sh
ffmpeg -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 -i FACE_CAM ...
```

That forces the USB input path to use MJPEG, avoiding the high USB bandwidth
cost of raw camera capture.

The active relay process should look roughly like this while a call is using
the proxy:

```sh
ffmpeg ... -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 \
  -i /dev/v4l/by-id/...Facecam... \
  -vf format=yuyv422 -s 1280x720 -r 30 -pix_fmt yuyv422 -f v4l2 /dev/video6
```

`ffmpeg` does the MJPEG decode and raw V4L2 output, so it is the process that
will use CPU during a call. The Python relay should stay near idle.

Avoid `FFMPEG_INPUT_EXTRA="-c:v mjpeg_qsv"` unless you have tested it locally.
Some Intel/ffmpeg stacks advertise the QSV MJPEG decoder but fail at runtime
with repeated `Error during QSV decoding` messages. If that happens, remove the
setting and restart the relay.
