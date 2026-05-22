"""Button integration placeholder.

The MVP does not need buttons. This module exists so future play/pause,
volume, and next-track GPIO work has a clear home.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ButtonController:
    enabled: bool = False

    def poll(self) -> None:
        return None
