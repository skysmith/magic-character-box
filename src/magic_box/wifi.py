"""Wi-Fi controls for the local admin UI."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil
import subprocess
from typing import Callable


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int | None = None
    security: str = ""
    active: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ssid": self.ssid,
            "signal": self.signal,
            "security": self.security,
            "active": self.active,
        }


@dataclass(frozen=True)
class WifiStatus:
    available: bool
    message: str
    powered: bool | None = None
    connected: bool = False
    ssid: str = ""
    device: str = ""
    networks: tuple[WifiNetwork, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "message": self.message,
            "powered": self.powered,
            "connected": self.connected,
            "ssid": self.ssid,
            "device": self.device,
            "networks": [network.to_dict() for network in self.networks],
        }


@dataclass(frozen=True)
class WifiActionResult:
    ok: bool
    message: str
    status: WifiStatus

    def to_dict(self) -> dict[str, object]:
        payload = self.status.to_dict()
        payload.update({"ok": self.ok, "message": self.message})
        return payload


class WifiController:
    """Small wrapper around NetworkManager's nmcli."""

    def __init__(
        self,
        nmcli: str | None = None,
        helper: str | None = None,
        sudo: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.nmcli = nmcli if nmcli is not None else shutil.which("nmcli")
        self.helper = helper if helper is not None else os.getenv("MAGIC_BOX_WIFI_HELPER") or shutil.which("magic-character-box-wifi-control")
        self.sudo = sudo if sudo is not None else shutil.which("sudo")
        self._runner = runner

    def status(self) -> WifiStatus:
        if self.nmcli is None and self.helper is None:
            return WifiStatus(available=False, message="Wi-Fi controls need NetworkManager nmcli on the Pi.")

        radio = self._run("radio", "wifi", timeout=4)
        if radio.returncode != 0:
            return WifiStatus(available=False, message=_clean_error(radio) or "Could not read Wi-Fi radio status.")

        powered = radio.stdout.strip().lower() == "enabled"
        device_status = self._run("-t", "--escape", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status", timeout=5)
        if device_status.returncode != 0:
            return WifiStatus(
                available=False,
                message=_clean_error(device_status) or "Could not read Wi-Fi device status.",
                powered=powered,
            )

        device = ""
        ssid = ""
        connected = False
        for line in device_status.stdout.splitlines():
            fields = _split_nmcli(line)
            if len(fields) < 4 or fields[1] != "wifi":
                continue
            device = fields[0]
            connected = fields[2] == "connected"
            ssid = fields[3] if connected and fields[3] != "--" else ""
            break

        if not powered:
            message = "Wi-Fi radio is off."
        elif connected:
            message = f"Connected to {ssid}." if ssid else "Wi-Fi is connected."
        elif device:
            message = "Wi-Fi is on but not connected."
        else:
            message = "No Wi-Fi device found."

        return WifiStatus(
            available=bool(device),
            message=message,
            powered=powered,
            connected=connected,
            ssid=ssid,
            device=device,
        )

    def scan(self) -> WifiActionResult:
        if self.nmcli is None and self.helper is None:
            return self._missing_result()

        self._run("radio", "wifi", "on", timeout=8)
        self._run("device", "wifi", "rescan", timeout=12)
        listing = self._run(
            "-t",
            "--escape",
            "yes",
            "-f",
            "IN-USE,SSID,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            timeout=12,
        )
        if listing.returncode != 0:
            status = self.status()
            return WifiActionResult(
                ok=False,
                message=_clean_error(listing) or "Could not scan Wi-Fi networks.",
                status=status,
            )

        status = self.status()
        networks = tuple(_parse_wifi_networks(listing.stdout))
        status = WifiStatus(
            available=status.available,
            message="Wi-Fi scan complete.",
            powered=status.powered,
            connected=status.connected,
            ssid=status.ssid,
            device=status.device,
            networks=networks,
        )
        return WifiActionResult(ok=True, message="Wi-Fi scan complete.", status=status)

    def connect(self, ssid: str, password: str = "") -> WifiActionResult:
        if self.nmcli is None and self.helper is None:
            return self._missing_result()

        clean_ssid = ssid.strip()
        if not clean_ssid:
            raise ValueError("Choose a Wi-Fi network name.")
        if "\x00" in clean_ssid or "\n" in clean_ssid or "\r" in clean_ssid:
            raise ValueError("Wi-Fi network name contains unsupported characters.")
        if "\x00" in password or "\n" in password or "\r" in password:
            raise ValueError("Wi-Fi password contains unsupported characters.")

        args = ["device", "wifi", "connect", clean_ssid]
        if password:
            args.extend(["password", password])
        completed = self._run(*args, timeout=30)
        ok = completed.returncode == 0
        message = f"Connected to {clean_ssid}." if ok else _clean_error(completed) or f"Could not connect to {clean_ssid}."
        return WifiActionResult(ok=ok, message=message, status=self.status())

    def _missing_result(self) -> WifiActionResult:
        status = self.status()
        return WifiActionResult(ok=False, message=status.message, status=status)

    def _run(self, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        command = self._command_for_args(args)
        try:
            return self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("nmcli command failed: %s", exc)
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def _command_for_args(self, args: tuple[str, ...]) -> list[str]:
        helper_args = _helper_args(args)
        if helper_args is not None and self.helper is not None:
            command = [self.helper, *helper_args]
            if os.geteuid() != 0 and self.sudo:
                return [self.sudo, "-n", *command]
            return command
        if self.nmcli is None:
            raise RuntimeError("NetworkManager nmcli is not installed.")
        return [self.nmcli, *args]


def _parse_wifi_networks(output: str) -> list[WifiNetwork]:
    by_ssid: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        fields = _split_nmcli(line)
        if len(fields) < 4:
            continue
        ssid = fields[1].strip()
        if not ssid:
            continue
        signal = _parse_int(fields[2])
        network = WifiNetwork(
            ssid=ssid,
            signal=signal,
            security=fields[3].strip(),
            active=fields[0].strip() == "*",
        )
        existing = by_ssid.get(ssid)
        if existing is None or (network.signal or 0) > (existing.signal or 0) or network.active:
            by_ssid[ssid] = network
    return sorted(by_ssid.values(), key=lambda item: (not item.active, -(item.signal or -1), item.ssid.lower()))


def _helper_args(args: tuple[str, ...]) -> list[str] | None:
    if args == ("radio", "wifi"):
        return ["radio-status"]
    if args == ("radio", "wifi", "on"):
        return ["radio-on"]
    if args == ("device", "wifi", "rescan"):
        return ["rescan"]
    if args == ("-t", "--escape", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
        return ["device-status"]
    if args == ("-t", "--escape", "yes", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"):
        return ["list"]
    if len(args) == 4 and args[:3] == ("device", "wifi", "connect"):
        return ["connect", args[3]]
    if len(args) == 6 and args[:3] == ("device", "wifi", "connect") and args[4] == "password":
        return ["connect", args[3], args[5]]
    return None


def _split_nmcli(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.rstrip("\n"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


def _clean_error(completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or completed.stdout or "").strip()
    return output.splitlines()[-1] if output else ""
