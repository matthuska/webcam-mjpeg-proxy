#!/usr/bin/env python3
"""Shared TOML config helpers for the MJPEG camera loopback tools."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("./mjpeg-camera-loopback.toml")


@dataclass(frozen=True)
class CameraProfile:
    name: str
    glob: str
    controls: dict[str, str]


@dataclass(frozen=True)
class Config:
    config_path: Path
    loopback_device: str
    loopback_video_nr: int
    loopback_label: str
    loopback_exclusive_caps: bool
    width: int
    height: int
    fps: int
    placeholder_fps: int
    placeholder_quality: int
    idle_timeout_seconds: float
    poll_interval_seconds: float
    selection_order: list[str]
    cameras: dict[str, CameraProfile]


@dataclass(frozen=True)
class SelectedCamera:
    profile: CameraProfile
    device: str


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")
    return value


def _str_value(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _int_value(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _float_value(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    parsed = float(value)
    if parsed < 0:
        raise ValueError(f"{key} must not be negative")
    return parsed


def _bool_value(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _control_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"control values must be strings, integers, floats, or booleans, got {value!r}")


def _default_data() -> dict[str, Any]:
    return {
        "loopback": {
            "device": "/dev/video6",
            "video_nr": 6,
            "label": "MJPEG Camera Proxy",
            "exclusive_caps": True,
        },
        "capture": {"width": 1280, "height": 720, "fps": 30},
        "placeholder": {"fps": 1, "mjpeg_quality": 31},
        "relay": {"idle_timeout_seconds": 5.0, "poll_interval_seconds": 0.5},
        "selection": {"order": ["facecam", "brio"]},
        "cameras": {
            "facecam": {
                "glob": "/dev/v4l/by-id/*Elgato*Facecam*-video-index0",
                "controls": {"power_line_frequency": 1, "zoom_absolute": 4},
            },
            "brio": {
                "glob": "/dev/v4l/by-id/*Logitech*BRIO*-video-index0",
                "controls": {"power_line_frequency": 1, "zoom_absolute": 129},
            },
        },
    }


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    data = _default_data()
    if path.exists():
        with path.open("rb") as config_file:
            loaded = tomllib.load(config_file)
        data = loaded

    loopback = _section(data, "loopback")
    capture = _section(data, "capture")
    placeholder = _section(data, "placeholder")
    relay = _section(data, "relay")
    selection = _section(data, "selection")
    camera_tables = _section(data, "cameras")

    cameras: dict[str, CameraProfile] = {}
    for name, camera_value in camera_tables.items():
        if not isinstance(camera_value, dict):
            raise ValueError(f"[cameras.{name}] must be a table")
        controls_value = camera_value.get("controls", {})
        if not isinstance(controls_value, dict):
            raise ValueError(f"[cameras.{name}.controls] must be a table")
        controls = {str(key): _control_value(value) for key, value in controls_value.items()}
        cameras[name] = CameraProfile(
            name=name,
            glob=_str_value(camera_value, "glob", ""),
            controls=controls,
        )

    if not cameras:
        raise ValueError("at least one [cameras.<name>] profile is required")

    order_value = selection.get("order", list(cameras))
    if not isinstance(order_value, list) or not all(isinstance(item, str) for item in order_value):
        raise ValueError("selection.order must be a list of camera profile names")
    missing = [name for name in order_value if name not in cameras]
    if missing:
        raise ValueError(f"selection.order references unknown camera profile(s): {', '.join(missing)}")

    video_nr = _int_value(loopback, "video_nr", 6)
    return Config(
        config_path=path,
        loopback_device=_str_value(loopback, "device", f"/dev/video{video_nr}"),
        loopback_video_nr=video_nr,
        loopback_label=_str_value(loopback, "label", "MJPEG Camera Proxy"),
        loopback_exclusive_caps=_bool_value(loopback, "exclusive_caps", True),
        width=_int_value(capture, "width", 1280),
        height=_int_value(capture, "height", 720),
        fps=_int_value(capture, "fps", 30),
        placeholder_fps=_int_value(placeholder, "fps", 1),
        placeholder_quality=_int_value(placeholder, "mjpeg_quality", 31),
        idle_timeout_seconds=_float_value(relay, "idle_timeout_seconds", 5.0),
        poll_interval_seconds=_float_value(relay, "poll_interval_seconds", 0.5),
        selection_order=order_value,
        cameras=cameras,
    )


def camera_matches(profile: CameraProfile) -> list[str]:
    candidates = sorted(glob.glob(profile.glob))
    index0 = [candidate for candidate in candidates if "index0" in os.path.basename(candidate)]
    return index0 or candidates


def select_camera(config: Config) -> SelectedCamera | None:
    for name in config.selection_order:
        profile = config.cameras[name]
        matches = camera_matches(profile)
        if matches:
            return SelectedCamera(profile=profile, device=matches[0])
    return None


def advertised_controls(device: str) -> set[str]:
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-ctrls"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return set()
    controls = set()
    for line in result.stdout.splitlines():
        match = re.match(r"\s*([A-Za-z0-9_]+)\s+0x[0-9A-Fa-f]+", line)
        if match:
            controls.add(match.group(1))
    return controls


def sh_assign(name: str, value: str | int | bool) -> str:
    if isinstance(value, bool):
        rendered = "1" if value else "0"
    else:
        rendered = str(value)
    return f"{name}={shlex.quote(rendered)}"


def print_shell_loopback(config: Config) -> None:
    values: dict[str, str | int | bool] = {
        "LOOPBACK_VIDEO_NR": config.loopback_video_nr,
        "LOOPBACK_DEVICE": config.loopback_device,
        "LOOPBACK_LABEL": config.loopback_label,
        "LOOPBACK_EXCLUSIVE_CAPS": config.loopback_exclusive_caps,
        "WIDTH": config.width,
        "HEIGHT": config.height,
        "FPS": config.fps,
    }
    for key, value in values.items():
        print(sh_assign(key, value))


def print_json(config: Config) -> None:
    print(
        json.dumps(
            {
                "loopback": {
                    "device": config.loopback_device,
                    "video_nr": config.loopback_video_nr,
                    "label": config.loopback_label,
                    "exclusive_caps": config.loopback_exclusive_caps,
                },
                "capture": {"width": config.width, "height": config.height, "fps": config.fps},
                "selection": {"order": config.selection_order},
                "cameras": {
                    name: {"glob": profile.glob, "controls": profile.controls}
                    for name, profile in config.cameras.items()
                },
            },
            indent=2,
        )
    )


def run(command: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def capture(command: list[str]) -> str:
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return result.stdout.rstrip()


def section(title: str) -> None:
    print(f"\n== {title} ==")


def probe(config: Config) -> int:
    section("V4L2 Devices")
    print(capture(["v4l2-ctl", "--list-devices"]) or "No V4L2 devices reported.")

    section("Configured Cameras")
    for name in config.selection_order:
        profile = config.cameras[name]
        matches = camera_matches(profile)
        print(f"{name}: {profile.glob}")
        if matches:
            for match in matches:
                print(f"  match: {match}")
        else:
            print("  no matches")
        if profile.controls:
            controls = ", ".join(f"{key}={value}" for key, value in profile.controls.items())
            print(f"  controls: {controls}")

    selected = select_camera(config)
    if not selected:
        section("Selected Camera")
        print("No configured camera is currently connected.")
        return 1

    section("Selected Camera")
    print(f"profile: {selected.profile.name}")
    print(f"device: {selected.device}")

    section("Formats")
    formats = capture(["v4l2-ctl", "-d", selected.device, "--list-formats-ext"])
    print(formats)

    expected_size = f"{config.width}x{config.height}"
    if "MJPG" in formats and expected_size in formats:
        print(f"\nMJPEG {expected_size} appears to be supported. Check the interval list above for {config.fps} fps.")
        return 0

    print(f"\nMJPEG {expected_size} was not found in the advertised modes.", file=sys.stderr)
    return 1


def doctor(config: Config) -> int:
    section("Config")
    print(f"config: {config.config_path}")
    print(f"loopback: {config.loopback_device} ({config.loopback_label})")
    print(f"capture: MJPEG {config.width}x{config.height}@{config.fps}")
    print(f"camera order: {', '.join(config.selection_order)}")

    section("User")
    run(["id"])
    groups = capture(["id", "-nG"]).split()
    if "video" in groups:
        print("User is in the video group.")
    elif os.path.exists(config.loopback_device) and os.access(config.loopback_device, os.R_OK | os.W_OK):
        print(f"User is not in the video group, but has read/write access to {config.loopback_device}.")
    else:
        print("User is not in the video group. Desktop ACLs may still allow access, but group membership is safer.")

    section("Device Nodes")
    nodes = sorted(glob.glob("/dev/video*"))
    print("\n".join(nodes) if nodes else "No /dev/video* nodes visible.")

    section("Loopback Sysfs")
    sysfs = Path(f"/sys/class/video4linux/video{config.loopback_video_nr}")
    if sysfs.exists():
        for name in ("name", "dev"):
            path = sysfs / name
            if path.exists():
                print(path.read_text().strip())
    else:
        print(f"No sysfs entry for video{config.loopback_video_nr}.")

    section("Loopback Info")
    if os.path.exists(config.loopback_device):
        run(["v4l2-ctl", "-d", config.loopback_device, "--all"])
        run(["v4l2-ctl", "-d", config.loopback_device, "--list-formats-ext"])
        if shutil.which("getfacl"):
            run(["getfacl", "-p", config.loopback_device])
    else:
        print(f"{config.loopback_device} does not exist.")

    section("Relay Processes")
    processes = capture(["pgrep", "-af", "relay.py|ffmpeg .*v4l2"])
    print(processes or "No relay producer process found.")

    section("Configured Cameras")
    for name in config.selection_order:
        profile = config.cameras[name]
        matches = camera_matches(profile)
        print(f"{name}: {profile.glob}")
        print(f"  matches: {', '.join(matches) if matches else 'none'}")
        if matches:
            advertised = advertised_controls(matches[0])
            for control, value in profile.controls.items():
                status = "supported" if control in advertised else "not advertised"
                print(f"  control {control}={value}: {status}")

    selected = select_camera(config)
    section("Selected Camera")
    if selected:
        print(f"{selected.profile.name}: {selected.device}")
    else:
        print("No configured camera is currently connected. The relay should keep serving the placeholder.")

    section("Suggestions")
    if config.loopback_video_nr > 9:
        print("Try a low loopback number such as /dev/video6; some apps miss high-numbered nodes.")
    if os.path.exists(config.loopback_device):
        info = capture(["v4l2-ctl", "-d", config.loopback_device, "--info"])
        if "Video Capture" in info:
            print(f"{config.loopback_device} advertises Video Capture.")
        else:
            print(f"{config.loopback_device} may not advertise Video Capture. Make sure the relay placeholder is running.")
        formats = capture(["v4l2-ctl", "-d", config.loopback_device, "--list-formats-ext"])
        expected_size = f"{config.width}x{config.height}"
        if expected_size not in formats:
            print(f"{config.loopback_device} is not advertising {expected_size}. Stop the relay, rerun setup with --replace, then restart the relay.")
        if "MJPG" not in formats and "JPEG" not in formats:
            print(f"{config.loopback_device} is not advertising MJPG. Current format may be unsuitable for WebEx.")
    print("Restart WebEx after recreating the loopback device; camera lists are often cached at app startup.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("shell-loopback")
    subparsers.add_parser("json")
    subparsers.add_parser("probe")
    subparsers.add_parser("doctor")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.command == "shell-loopback":
            print_shell_loopback(config)
        elif args.command == "json":
            print_json(config)
        elif args.command == "probe":
            return probe(config)
        elif args.command == "doctor":
            return doctor(config)
        else:
            raise ValueError(f"unknown command {args.command!r}")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
