#!/usr/bin/env python3
"""Backward-compatible entry point. Prefer `nespresso brew espresso`."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

from nespresso.cli import main, normalize_legacy_argv  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(normalize_legacy_argv(sys.argv[1:])))
