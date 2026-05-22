"""Optional MAX98357A shutdown-pin mute gate."""

from __future__ import annotations

import logging
from typing import Protocol


LOGGER = logging.getLogger(__name__)


class AmpGate(Protocol):
    def mute(self) -> None:
        """Mute or shut down the amplifier."""

    def unmute(self) -> None:
        """Enable the amplifier."""

    def close(self) -> None:
        """Release any GPIO resources."""


class NoopAmpGate:
    def mute(self) -> None:
        return

    def unmute(self) -> None:
        return

    def close(self) -> None:
        return


class GpioAmpGate:
    """Control an active-high amp enable/shutdown pin using BCM GPIO numbering."""

    def __init__(self, gpio_pin: int, active_high: bool = True) -> None:
        from gpiozero import DigitalOutputDevice

        self.gpio_pin = gpio_pin
        self._device = DigitalOutputDevice(gpio_pin, active_high=active_high, initial_value=False)

    def mute(self) -> None:
        self._device.off()

    def unmute(self) -> None:
        self._device.on()

    def close(self) -> None:
        self.mute()
        self._device.close()


def create_amp_gate(gpio_pin: int | None) -> AmpGate:
    if gpio_pin is None:
        return NoopAmpGate()

    try:
        gate = GpioAmpGate(gpio_pin)
    except Exception as exc:  # pragma: no cover - depends on Raspberry Pi GPIO runtime.
        LOGGER.warning("Amp mute GPIO %s is unavailable: %s", gpio_pin, exc)
        return NoopAmpGate()

    gate.mute()
    LOGGER.info("Amp mute gate ready on GPIO%s", gpio_pin)
    return gate
