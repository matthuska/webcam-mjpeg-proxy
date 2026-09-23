#!/usr/bin/env python3
"""On-demand MJPEG camera relay for v4l2loopback."""

from __future__ import annotations

import argparse
import glob
import logging
import os
import signal
import shlex
import subprocess
import sys
import time
from pathlib import Path

from mjpeg_config import Config, SelectedCamera, advertised_controls, load_config, select_camera

LOG = logging.getLogger("mjpeg-camera-relay")


def resolve_device(path: str) -> str:
    return os.path.realpath(path)


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


def placeholder_command(config: Config) -> list[str]:
    size = f"{config.width}x{config.height}"
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:r={config.placeholder_fps}",
        "-c:v",
        "mjpeg",
        "-q:v",
        str(config.placeholder_quality),
        "-s",
        size,
        "-r",
        str(config.placeholder_fps),
        "-f",
        "v4l2",
        config.loopback_device,
    ]


def camera_command(config: Config, camera_device: str) -> list[str]:
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
        "mjpeg",
        "-video_size",
        size,
        "-framerate",
        str(config.fps),
        "-i",
        camera_device,
        "-c:v",
        "copy",
        "-f",
        "v4l2",
        config.loopback_device,
    ]


def apply_camera_controls(selected: SelectedCamera) -> None:
    if not selected.profile.controls:
        return

    advertised = advertised_controls(selected.device)
    for name, value in selected.profile.controls.items():
        control = f"{name}={value}"
        if advertised and name not in advertised:
            LOG.warning(
                "camera profile %s requested unsupported control %s on %s",
                selected.profile.name,
                name,
                selected.device,
            )
            continue
        command = ["v4l2-ctl", "-d", selected.device, f"--set-ctrl={control}"]
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            LOG.info("set %s control %s", selected.profile.name, control)
        else:
            message = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            LOG.warning("could not set %s control %s: %s", selected.profile.name, control, message)


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
    last_camera_warning = 0.0

    def handle_signal(signum, _frame) -> None:
        nonlocal stopping
        LOG.info("received signal %s", signum)
        stopping = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    def start_mode(mode: str) -> None:
        nonlocal config, last_camera_warning
        if mode == "placeholder":
            producer.start("placeholder", placeholder_command(config))
            return
        if mode == "camera":
            config = load_config(config.config_path)
            selected = select_camera(config)
            if not selected:
                now = time.monotonic()
                if now - last_camera_warning > 10:
                    LOG.warning("no configured camera found; continuing placeholder")
                    last_camera_warning = now
                if producer.mode != "placeholder":
                    producer.start("placeholder", placeholder_command(config))
                return
            LOG.info("using camera profile %s device %s", selected.profile.name, selected.device)
            apply_camera_controls(selected)
            producer.start("camera", camera_command(config, selected.device))
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
    parser.add_argument("--config", type=Path, default=Path("./mjpeg-camera-loopback.toml"))
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
