"""Setup/playback mode controls for the Pi admin dashboard."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
import time
from typing import Callable


@dataclass(frozen=True)
class ModeStatus:
    available: bool
    playback_active: bool | None
    message: str

    @property
    def mode(self) -> str:
        if not self.available:
            return "local"
        return "playback" if self.playback_active else "setup"

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "mode": self.mode,
            "playback_active": self.playback_active,
            "message": self.message,
        }


@dataclass(frozen=True)
class ModeActionResult:
    ok: bool
    message: str
    status: ModeStatus

    def to_dict(self) -> dict[str, object]:
        payload = self.status.to_dict()
        payload.update({"ok": self.ok, "message": self.message})
        return payload


class ServiceModeController:
    """Switch between child playback mode and setup/admin scan mode."""

    def __init__(
        self,
        playback_service: str = "magic-character-box",
        systemctl: str | None = None,
        sudo: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.playback_service = playback_service
        self.systemctl = systemctl if systemctl is not None else shutil.which("systemctl")
        self.sudo = sudo if sudo is not None else shutil.which("sudo")
        self._runner = runner

    def status(self) -> ModeStatus:
        if self.systemctl is None:
            return ModeStatus(
                available=False,
                playback_active=None,
                message="Mode switching is available on the Raspberry Pi service install.",
            )

        completed = self._run_systemctl("is-active", "--quiet", self.playback_service, use_sudo=False)
        if completed.returncode == 0:
            return ModeStatus(
                available=True,
                playback_active=True,
                message="Playback mode: the box is listening for character tags.",
            )
        if completed.returncode == 3:
            return ModeStatus(
                available=True,
                playback_active=False,
                message="Setup mode: browser scanning can use the NFC reader.",
            )
        return ModeStatus(
            available=True,
            playback_active=None,
            message=_clean_output(completed) or "Could not read playback service status.",
        )

    def enter_setup(self) -> ModeActionResult:
        if self.systemctl is None:
            return self._unavailable_result()

        completed = self._run_systemctl("stop", self.playback_service)
        ok = completed.returncode == 0
        message = (
            "Setup mode is on. The browser Scan button can use the NFC reader."
            if ok
            else _clean_output(completed) or "Could not stop playback mode."
        )
        return ModeActionResult(ok=ok, message=message, status=self._wait_for_status(playback_active=False))

    def enter_playback(self) -> ModeActionResult:
        if self.systemctl is None:
            return self._unavailable_result()

        completed = self._run_systemctl("start", self.playback_service)
        ok = completed.returncode == 0
        message = (
            "Playback mode is on. Place a character on the box to play audio."
            if ok
            else _clean_output(completed) or "Could not start playback mode."
        )
        return ModeActionResult(ok=ok, message=message, status=self._wait_for_status(playback_active=True))

    def _wait_for_status(self, playback_active: bool, timeout: float = 4.0) -> ModeStatus:
        deadline = time.monotonic() + timeout
        status = self.status()
        while time.monotonic() < deadline:
            if status.playback_active is playback_active:
                return status
            time.sleep(0.25)
            status = self.status()
        return status

    def _unavailable_result(self) -> ModeActionResult:
        status = self.status()
        return ModeActionResult(ok=False, message=status.message, status=status)

    def _run_systemctl(self, *args: str, use_sudo: bool = True) -> subprocess.CompletedProcess[str]:
        assert self.systemctl is not None
        command = [self.systemctl, *args]
        if use_sudo and os.geteuid() != 0 and self.sudo is not None:
            command = [self.sudo, "-n", *command]
        try:
            return self._runner(command, check=False, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "Command timed out.")
        except OSError as exc:
            return subprocess.CompletedProcess(command, 127, "", str(exc))


def _clean_output(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    return text.splitlines()[-1].strip() if text else ""
