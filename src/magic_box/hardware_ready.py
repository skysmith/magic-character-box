"""Wait for unprivileged access to the player hardware device nodes."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time


DEFAULT_DEVICE_PATHS = (Path("/dev/gpiomem"), Path("/dev/spidev0.0"))
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class HardwareWaitResult:
    """The result of waiting for all required player device nodes."""

    ready: bool
    unavailable: tuple[Path, ...]


def device_is_readable_writable(path: Path) -> bool:
    """Return whether the current process can read and write ``path``."""

    return os.access(path, os.R_OK | os.W_OK)


def wait_for_player_hardware(
    device_paths: Sequence[Path] = DEFAULT_DEVICE_PATHS,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    access_check: Callable[[Path], bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> HardwareWaitResult:
    """Wait until every device is readable and writable by this process.

    The injected clock, sleeper, and access check keep delayed-permission and
    timeout behavior deterministic in unit tests. The systemd unit runs this
    function as the same unprivileged user and groups as the player.
    """

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    paths = tuple(Path(path) for path in device_paths)
    check = access_check or device_is_readable_writable
    clock = monotonic or time.monotonic
    pause = sleep or time.sleep
    deadline = clock() + timeout_seconds

    while True:
        unavailable = tuple(path for path in paths if not check(path))
        if not unavailable:
            return HardwareWaitResult(ready=True, unavailable=())

        remaining = deadline - clock()
        if remaining <= 0:
            return HardwareWaitResult(ready=False, unavailable=unavailable)
        pause(min(poll_interval_seconds, remaining))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Wait until the player process can read and write its GPIO and SPI device nodes."
        )
    )
    parser.add_argument(
        "devices",
        nargs="*",
        type=Path,
        default=DEFAULT_DEVICE_PATHS,
        help="Device paths to check. Defaults to /dev/gpiomem and /dev/spidev0.0.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Maximum seconds to wait. Default: {DEFAULT_TIMEOUT_SECONDS:g}.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Seconds between checks. Default: {DEFAULT_POLL_INTERVAL_SECONDS:g}.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = wait_for_player_hardware(
            args.devices,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if result.ready:
        print("Player hardware access is ready.")
        return 0

    unavailable = ", ".join(str(path) for path in result.unavailable)
    print(
        f"Timed out waiting for readable and writable player hardware: {unavailable}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
