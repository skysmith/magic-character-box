"""Bluetooth speaker controls for the local admin UI."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Callable


LOGGER = logging.getLogger(__name__)
BLUETOOTH_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    audio: bool = False
    icon: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "address": self.address,
            "name": self.name,
            "paired": self.paired,
            "trusted": self.trusted,
            "connected": self.connected,
            "audio": self.audio,
            "icon": self.icon,
        }


@dataclass(frozen=True)
class BluetoothStatus:
    available: bool
    message: str
    powered: bool | None = None
    discovering: bool | None = None
    default_sink: str | None = None
    devices: tuple[BluetoothDevice, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "message": self.message,
            "powered": self.powered,
            "discovering": self.discovering,
            "default_sink": self.default_sink,
            "devices": [device.to_dict() for device in self.devices],
        }


@dataclass(frozen=True)
class BluetoothActionResult:
    ok: bool
    message: str
    status: BluetoothStatus

    def to_dict(self) -> dict[str, object]:
        payload = self.status.to_dict()
        payload.update({"ok": self.ok, "message": self.message})
        return payload


class BluetoothController:
    """Small wrapper around BlueZ bluetoothctl plus PipeWire/Pulse defaults."""

    def __init__(
        self,
        bluetoothctl: str | None = None,
        pactl: str | None = None,
        wpctl: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.bluetoothctl = bluetoothctl if bluetoothctl is not None else shutil.which("bluetoothctl")
        self.pactl = pactl if pactl is not None else shutil.which("pactl")
        self.wpctl = wpctl if wpctl is not None else shutil.which("wpctl")
        self._runner = runner

    def status(self) -> BluetoothStatus:
        if self.bluetoothctl is None:
            return BluetoothStatus(
                available=False,
                message="Bluetooth controls need bluetoothctl on the Pi.",
                default_sink=self.default_sink(),
            )

        show = self._run_bluetoothctl("show", timeout=4)
        if show.returncode != 0:
            return BluetoothStatus(
                available=False,
                message=_clean_error(show) or "Bluetooth adapter is not available.",
                default_sink=self.default_sink(),
            )

        adapter = _parse_key_values(show.stdout)
        devices = self._known_devices()
        return BluetoothStatus(
            available=True,
            message="Bluetooth adapter ready.",
            powered=_parse_bool(adapter.get("Powered")),
            discovering=_parse_bool(adapter.get("Discovering")),
            default_sink=self.default_sink(),
            devices=tuple(sorted(devices, key=lambda device: (not device.connected, device.name.lower()))),
        )

    def power(self, enabled: bool) -> BluetoothActionResult:
        if self.bluetoothctl is None:
            return self._missing_result()

        completed = self._run_bluetoothctl("power", "on" if enabled else "off", timeout=8)
        ok = completed.returncode == 0
        message = f"Bluetooth power {'on' if enabled else 'off'}." if ok else _clean_error(completed)
        return BluetoothActionResult(ok=ok, message=message, status=self.status())

    def scan(self, timeout: int = 8) -> BluetoothActionResult:
        if self.bluetoothctl is None:
            return self._missing_result()

        timeout = min(max(int(timeout), 3), 30)
        self.power(True)
        completed = self._run_bluetoothctl("--timeout", str(timeout), "scan", "on", timeout=timeout + 4)
        if completed.returncode != 0 and _looks_like_bad_timeout_option(completed):
            completed = self._scan_with_process_timeout(timeout)
        self._run_bluetoothctl("scan", "off", timeout=4)

        ok = completed.returncode == 0
        message = (
            "Scan complete. Choose a speaker below."
            if ok
            else _clean_error(completed) or "Bluetooth scan failed."
        )
        return BluetoothActionResult(ok=ok, message=message, status=self.status())

    def pair(self, address: str) -> BluetoothActionResult:
        return self._device_action(address, "pair", "Paired")

    def trust(self, address: str) -> BluetoothActionResult:
        return self._device_action(address, "trust", "Trusted")

    def connect(self, address: str) -> BluetoothActionResult:
        return self._device_action(address, "connect", "Connected")

    def disconnect(self, address: str) -> BluetoothActionResult:
        return self._device_action(address, "disconnect", "Disconnected")

    def use_for_audio(self, address: str) -> BluetoothActionResult:
        normalized = normalize_bluetooth_address(address)
        current = next((device for device in self.status().devices if device.address == normalized), None)
        if current is not None and not current.paired:
            pair = self.pair(normalized)
            if not pair.ok:
                return pair
        trust = self.trust(normalized)
        connect = self.connect(normalized)
        if not connect.ok:
            return connect

        sink_name = self.set_default_sink_for_device(normalized)
        if sink_name:
            return BluetoothActionResult(
                ok=True,
                message=f"Connected and set audio output to {sink_name}.",
                status=self.status(),
            )

        if trust.ok:
            message = "Connected. Could not automatically set the default audio output."
        else:
            message = "Connected. Trusting the speaker failed, and the default audio output was not changed."
        return BluetoothActionResult(ok=True, message=message, status=self.status())

    def default_sink(self) -> str | None:
        if self.wpctl is not None:
            completed = self._run([self.wpctl, "inspect", "@DEFAULT_AUDIO_SINK@"], timeout=4)
            if completed.returncode == 0:
                values = _parse_key_values(completed.stdout)
                return values.get("node.description") or values.get("node.name")

        if self.pactl is not None:
            completed = self._run([self.pactl, "info"], timeout=4)
            if completed.returncode == 0:
                values = _parse_colon_values(completed.stdout)
                return values.get("Default Sink")

        return None

    def set_default_sink_for_device(self, address: str, wait_seconds: float = 6.0) -> str | None:
        normalized = normalize_bluetooth_address(address)
        needle = f"bluez_output.{normalized.replace(':', '_')}".lower()
        deadline = time.monotonic() + max(wait_seconds, 0)

        while True:
            sink = self._find_pulse_sink(needle)
            if sink:
                completed = self._run([self.pactl, "set-default-sink", sink], timeout=6)
                if completed.returncode == 0:
                    return sink
                LOGGER.warning("Could not set Bluetooth sink %s as default: %s", sink, _clean_error(completed))
                return None

            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def _device_action(self, address: str, action: str, label: str) -> BluetoothActionResult:
        if self.bluetoothctl is None:
            return self._missing_result()

        normalized = normalize_bluetooth_address(address)
        completed = self._run_bluetoothctl(action, normalized, timeout=15)
        ok = completed.returncode == 0
        message = f"{label} {normalized}." if ok else _clean_error(completed) or f"Could not {action} {normalized}."
        return BluetoothActionResult(ok=ok, message=message, status=self.status())

    def _known_devices(self) -> list[BluetoothDevice]:
        if self.bluetoothctl is None:
            return []

        devices = _parse_device_rows(self._run_bluetoothctl("devices", timeout=6).stdout)
        for address in list(devices):
            info = self._run_bluetoothctl("info", address, timeout=6)
            if info.returncode == 0:
                devices[address] = _device_from_info(address, devices[address], info.stdout)
        return list(devices.values())

    def _find_pulse_sink(self, needle: str) -> str | None:
        if self.pactl is None:
            return None

        completed = self._run([self.pactl, "list", "short", "sinks"], timeout=5)
        if completed.returncode != 0:
            LOGGER.warning("Could not list Pulse/PipeWire sinks: %s", _clean_error(completed))
            return None

        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and needle in parts[1].lower():
                return parts[1]
        return None

    def _scan_with_process_timeout(self, timeout: int) -> subprocess.CompletedProcess[str]:
        assert self.bluetoothctl is not None
        process = subprocess.Popen(
            [self.bluetoothctl, "scan", "on"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate(timeout=3)
        return subprocess.CompletedProcess(process.args, process.returncode or 0, stdout, stderr)

    def _missing_result(self) -> BluetoothActionResult:
        return BluetoothActionResult(
            ok=False,
            message="Bluetooth controls need bluetoothctl on the Pi.",
            status=self.status(),
        )

    def _run_bluetoothctl(self, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        assert self.bluetoothctl is not None
        return self._run([self.bluetoothctl, *args], timeout=timeout)

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        try:
            return self._runner(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(args, 124, exc.stdout or "", exc.stderr or "Command timed out.")
        except OSError as exc:
            return subprocess.CompletedProcess(args, 127, "", str(exc))


def normalize_bluetooth_address(value: str) -> str:
    address = value.strip().upper()
    if not BLUETOOTH_ADDRESS_RE.match(address):
        raise ValueError("Bluetooth address must look like AA:BB:CC:DD:EE:FF.")
    return address


def _device_from_info(address: str, fallback: BluetoothDevice, text: str) -> BluetoothDevice:
    values = _parse_key_values(text)
    name = values.get("Name") or values.get("Alias") or fallback.name or address
    icon = values.get("Icon", "")
    audio = _looks_like_audio_device(text, icon)
    return BluetoothDevice(
        address=address,
        name=name,
        paired=_parse_bool(values.get("Paired")) is True,
        trusted=_parse_bool(values.get("Trusted")) is True,
        connected=_parse_bool(values.get("Connected")) is True,
        audio=audio,
        icon=icon,
    )


def _parse_device_rows(text: str) -> dict[str, BluetoothDevice]:
    devices: dict[str, BluetoothDevice] = {}
    for line in text.splitlines():
        match = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", line.strip())
        if not match:
            continue
        address = normalize_bluetooth_address(match.group(1))
        devices[address] = BluetoothDevice(address=address, name=match.group(2).strip() or address)
    return devices


def _parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if " = " in stripped:
            key, raw_value = stripped.split(" = ", 1)
        elif ":" in stripped:
            key, raw_value = stripped.split(":", 1)
        else:
            continue
        values[key.strip().strip('"')] = raw_value.strip().strip('"')
    return values


def _parse_colon_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        values[key.strip()] = raw_value.strip()
    return values


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"yes", "true", "on"}:
        return True
    if lowered in {"no", "false", "off"}:
        return False
    return None


def _looks_like_audio_device(text: str, icon: str) -> bool:
    lowered = f"{text}\n{icon}".lower()
    return any(
        marker in lowered
        for marker in (
            "audio sink",
            "audio-source",
            "audio-card",
            "audio-headset",
            "headset",
            "speaker",
            "a2dp",
        )
    )


def _clean_error(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    return text.splitlines()[-1].strip() if text else ""


def _looks_like_bad_timeout_option(completed: subprocess.CompletedProcess[str]) -> bool:
    text = f"{completed.stdout}\n{completed.stderr}".lower()
    return "--timeout" in text and any(marker in text for marker in ("unknown", "invalid", "unrecognized"))
