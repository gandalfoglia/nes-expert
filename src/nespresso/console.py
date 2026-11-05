"""Small colored terminal writer. Color is skipped when stdout is not a terminal."""

from __future__ import annotations

import os
import sys


class Console:
    def __init__(self, color: bool = True) -> None:
        self.color = color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()

    def _paint(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def progress(self, message: str) -> None:
        print(self._paint(message, "36"), flush=True)

    def info(self, message: str) -> None:
        print(message, flush=True)

    def success(self, message: str) -> None:
        print(self._paint(message, "32"), flush=True)

    def warn(self, message: str) -> None:
        print(self._paint(message, "33"), file=sys.stderr, flush=True)

    def error(self, message: str) -> None:
        print(self._paint(message, "31"), file=sys.stderr, flush=True)

    def table(self, rows: list[tuple[str, str]]) -> None:
        if not rows:
            return
        width = max(len(label) for label, _value in rows)
        for label, value in rows:
            painted = self._paint(label.ljust(width), "2")
            print(f"  {painted}  {value}", flush=True)

    def device(self, address: str, name: str, rssi: int | None, likely: bool) -> None:
        label = name or "(no name)"
        signal = f"  {rssi} dBm" if rssi is not None else ""
        mark = self._paint("●", "32") if likely else self._paint("·", "2")
        shown_name = self._paint(label, "1") if likely else label
        print(f"  {mark}  {shown_name}{signal}", flush=True)
        print(f"     {self._paint(address, '2')}", flush=True)
