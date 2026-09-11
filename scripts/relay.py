#!/usr/bin/env python3
"""On-demand MJPEG Facecam relay for v4l2loopback."""

from __future__ import annotations

import argparse
import glob
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


LOG = logging.getLogger("facecam-mjpeg-relay")


@dataclass(frozen=True)
class Config:
    facecam_device: str
    facecam_device_glob: str
    loopback_device: str
    width: int
    height: int
    fps: int
    input_format: str
    output_mode: str
    output_pixel_format_ffmpeg: str
    placeholder_fps: int
    mjpeg_placeholder_quality: int
    idle_timeout_seconds: float
    poll_interval_seconds: float
    ffmpeg_input_extra: tuple[str, ...]
    ffmpeg_output_extra: tuple[str, ...]


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_no, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_no}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"{path}:{line_no}: invalid key {key!r}")
        parsed = shlex.split(value, comments=True, posix=True)
        value = " ".join(parsed)
        values[key] = value
    return values


def getenv(values: dict[str, str], key: str, default: str) -> str:
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    return values.get(key, default)


def positive_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def nonnegative_float(name: str, value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must not be negative")
    return parsed


def parse_output_mode(value: str) -> str:
    parsed = value.lower()
    if parsed not in {"raw", "mjpeg"}:
        raise ValueError("OUTPUT_MODE must be 'raw' or 'mjpeg'")
    return parsed


def load_config(path: Path) -> Config:
    values = parse_env_file(path)
    facecam_glob = getenv(values, "FACECAM_DEVICE_GLOB", "/dev/v4l/by-id/*Elgato*Facecam*")
    return Config(
        facecam_device=getenv(values, "FACECAM_DEVICE", ""),
        facecam_device_glob=facecam_glob,
        loopback_device=getenv(values, "LOOPBACK_DEVICE", "/dev/video42"),
        width=positive_int("WIDTH", getenv(values, "WIDTH", "1280")),
        height=positive_int("HEIGHT", getenv(values, "HEIGHT", "720")),
        fps=positive_int("FPS", getenv(values, "FPS", "30")),
        input_format=getenv(values, "INPUT_FORMAT", "mjpeg"),
        output_mode=parse_output_mode(getenv(values, "OUTPUT_MODE", "raw")),
        output_pixel_format_ffmpeg=getenv(values, "OUTPUT_PIXEL_FORMAT_FFMPEG", "yuyv422"),
        placeholder_fps=positive_int("PLACEHOLDER_FPS", getenv(values, "PLACEHOLDER_FPS", "5")),
        mjpeg_placeholder_quality=positive_int(
            "MJPEG_PLACEHOLDER_QUALITY", getenv(values, "MJPEG_PLACEHOLDER_QUALITY", "31")
        ),
        idle_timeout_seconds=nonnegative_float(
            "IDLE_TIMEOUT_SECONDS", getenv(values, "IDLE_TIMEOUT_SECONDS", "5")
        ),
        poll_interval_seconds=nonnegative_float(
            "POLL_INTERVAL_SECONDS", getenv(values, "POLL_INTERVAL_SECONDS", "0.5")
        ),
        ffmpeg_input_extra=tuple(shlex.split(getenv(values, "FFMPEG_INPUT_EXTRA", ""))),
        ffmpeg_output_extra=tuple(shlex.split(getenv(values, "FFMPEG_OUTPUT_EXTRA", ""))),
    )


def resolve_device(path: str) -> str:
    return os.path.realpath(path)


def find_facecam(config: Config) -> str | None:
    if config.facecam_device:
        return config.facecam_device if os.path.exists(config.facecam_device) else None

    candidates = sorted(glob.glob(config.facecam_device_glob))
    index0 = [candidate for candidate in candidates if "index0" in os.path.basename(candidate)]
    if index0:
        return index0[0]
    if candidates:
        return candidates[0]
    return None


def process_name(pid: int) -> str:
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return "unknown"
    return comm or "unknown"


def pids_holding_device(device: str, ignored_pids: set[int]) -> dict[int, str]:
    target = resolve_device(device)
    holders: dict[int, str] = {}
    for proc_path in glob.glob("/proc/[0-9]*"):
        pid = int(os.path.basename(proc_path))
        if pid in ignored_pids:
            continue
        fd_dir = os.path.join(proc_path, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            fd_path = os.path.join(fd_dir, fd)
            try:
                if resolve_device(fd_path) == target:
                    holders[pid] = process_name(pid)
                    break
            except OSError:
                continue
    return holders


class Producer:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.mode = "stopped"

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process and self.process.poll() is None else None

    def start(self, mode: str, command: list[str]) -> None:
        self.stop()
        LOG.info("starting %s producer", mode)
        LOG.debug("producer command: %s", shlex.join(command))
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        self.mode = mode

    def stop(self) -> None:
        if not self.process:
            self.mode = "stopped"
            return
        if self.process.poll() is None:
            LOG.info("stopping %s producer", self.mode)
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
        self.mode = "stopped"

    def restart_if_dead(self, command_builder) -> None:
        if self.process and self.process.poll() is not None:
            LOG.warning("%s producer exited with code %s", self.mode, self.process.returncode)
            mode = self.mode
            self.process = None
            self.mode = "stopped"
            time.sleep(2)
            command_builder(mode)


def raw_placeholder_command(config: Config) -> list[str]:
    size = f"{config.width}x{config.height}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r={config.placeholder_fps}",
        "-vf",
        f"format={config.output_pixel_format_ffmpeg}",
        "-s",
        size,
        "-r",
        str(config.fps),
        "-pix_fmt",
        config.output_pixel_format_ffmpeg,
        "-f",
        "v4l2",
        *config.ffmpeg_output_extra,
        config.loopback_device,
    ]


def raw_camera_command(config: Config, facecam_device: str) -> list[str]:
    size = f"{config.width}x{config.height}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-f",
        "v4l2",
        "-input_format",
        config.input_format,
        "-video_size",
        size,
        "-framerate",
        str(config.fps),
        *config.ffmpeg_input_extra,
        "-i",
        facecam_device,
        "-vf",
        f"format={config.output_pixel_format_ffmpeg}",
        "-s",
        size,
        "-r",
        str(config.fps),
        "-pix_fmt",
        config.output_pixel_format_ffmpeg,
        "-f",
        "v4l2",
        *config.ffmpeg_output_extra,
        config.loopback_device,
    ]


def ffmpeg_mjpeg_placeholder_command(config: Config) -> list[str]:
    size = f"{config.width}x{config.height}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r={config.placeholder_fps}",
        "-c:v",
        "mjpeg",
        "-q:v",
        str(config.mjpeg_placeholder_quality),
        "-s",
        size,
        "-r",
        str(config.placeholder_fps),
        "-f",
        "v4l2",
        *config.ffmpeg_output_extra,
        config.loopback_device,
    ]


def ffmpeg_mjpeg_camera_command(config: Config, facecam_device: str) -> list[str]:
    size = f"{config.width}x{config.height}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-f",
        "v4l2",
        "-input_format",
        config.input_format,
        "-video_size",
        size,
        "-framerate",
        str(config.fps),
        *config.ffmpeg_input_extra,
        "-i",
        facecam_device,
        "-c:v",
        "copy",
        "-f",
        "v4l2",
        *config.ffmpeg_output_extra,
        config.loopback_device,
    ]


def placeholder_command(config: Config) -> list[str]:
    if config.output_mode == "mjpeg":
        return ffmpeg_mjpeg_placeholder_command(config)
    return raw_placeholder_command(config)


def camera_command(config: Config, facecam_device: str) -> list[str]:
    if config.output_mode == "mjpeg":
        return ffmpeg_mjpeg_camera_command(config, facecam_device)
    return raw_camera_command(config, facecam_device)


def ensure_loopback_exists(config: Config) -> None:
    if not os.path.exists(config.loopback_device):
        raise FileNotFoundError(
            f"{config.loopback_device} does not exist. Run scripts/setup-loopback.sh first."
        )


def relay(config: Config) -> int:
    ensure_loopback_exists(config)
    producer = Producer()
    stopping = False
    last_consumer_seen = 0.0
    last_facecam_warning = 0.0

    def handle_signal(signum, _frame) -> None:
        nonlocal stopping
        LOG.info("received signal %s", signum)
        stopping = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    def start_mode(mode: str) -> None:
        nonlocal last_facecam_warning
        if mode == "placeholder":
            producer.start("placeholder", placeholder_command(config))
            return
        if mode == "camera":
            facecam = find_facecam(config)
            if not facecam:
                now = time.monotonic()
                if now - last_facecam_warning > 10:
                    LOG.warning("Facecam not found; continuing placeholder")
                    last_facecam_warning = now
                if producer.mode != "placeholder":
                    producer.start("placeholder", placeholder_command(config))
                return
            LOG.info("using Facecam device %s", facecam)
            producer.start("camera", camera_command(config, facecam))
            return
        raise ValueError(f"unknown mode {mode!r}")

    start_mode("placeholder")

    try:
        while not stopping:
            producer.restart_if_dead(start_mode)
            ignored = {os.getpid()}
            producer_pid = producer.pid
            if producer_pid is not None:
                ignored.add(producer_pid)

            consumers = pids_holding_device(config.loopback_device, ignored)
            now = time.monotonic()
            if consumers:
                last_consumer_seen = now
                if producer.mode != "camera":
                    names = ", ".join(f"{pid}/{name}" for pid, name in sorted(consumers.items()))
                    LOG.info("consumer detected: %s", names)
                    start_mode("camera")
            elif producer.mode == "camera" and now - last_consumer_seen >= config.idle_timeout_seconds:
                LOG.info("no consumers for %.1fs; returning to placeholder", config.idle_timeout_seconds)
                start_mode("placeholder")

            time.sleep(config.poll_interval_seconds)
    finally:
        producer.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("./facecam-loopback.env"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
        return relay(config)
    except Exception as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
