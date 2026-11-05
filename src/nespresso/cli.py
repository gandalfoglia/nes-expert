"""Command-line entry for an unofficial Nespresso Expert & Milk tool."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from nespresso import __version__
from nespresso.errors import NespressoError
from nespresso.protocol import DRINK_CHOICES, DRINK_ORDER, normalize_drink

EXAMPLES = """
examples:
  nespresso scan
  nespresso pair
  nespresso brew espresso
  nespresso brew lungo --temp high
  nespresso status
  nespresso stop
  nespresso tui
""".strip(
    "\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nespresso",
        description="Unofficial terminal control for a Nespresso Expert & Milk on macOS.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output. Also disabled when stdout is not a terminal or NO_COLOR is set.",
    )
    connect = argparse.ArgumentParser(add_help=False)
    connect.add_argument(
        "--address",
        help="Bluetooth address (on macOS this is a UUID). Defaults to the machine saved in ~/.nespresso_auth.json.",
    )
    connect.add_argument(
        "--reset-auth",
        action="store_true",
        help=(
            "Force a new pairing key. Factory-reset the machine first. "
            "`nespresso pair` always does this."
        ),
    )

    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    scan = commands.add_parser(
        "scan",
        parents=[common],
        help="Discover nearby Nespresso machines and show address and name.",
        description="Scan for nearby Nespresso machines.",
    )
    scan.add_argument(
        "--timeout",
        type=_positive_float,
        default=8.0,
        help="Seconds to scan (default: 8).",
    )
    scan.add_argument(
        "--all",
        action="store_true",
        help="Also list Bluetooth devices that do not look like a Nespresso machine.",
    )

    brew = commands.add_parser(
        "brew",
        parents=[common, connect],
        help="Connect, authenticate, and brew.",
        description="Brew a cup. The first successful pairing is saved for later one-shot commands.",
    )
    brew.add_argument(
        "drink",
        choices=DRINK_CHOICES,
        metavar="drink",
        help="ristretto, espresso, lungo, hotwater, or americano.",
    )
    brew.add_argument(
        "--temp",
        choices=["low", "medium", "high"],
        default="medium",
        help="Cup temperature: low, medium, or high (default: medium).",
    )
    brew.add_argument(
        "--no-prepare",
        action="store_true",
        help="Skip the optional prepare write before the brew frame.",
    )

    commands.add_parser(
        "status",
        parents=[common, connect],
        help="Connect and read basic machine state.",
        description="Connect, authenticate, and read pairing, water, slider, and version fields when the machine allows it.",
    )
    commands.add_parser(
        "pair",
        parents=[common, connect],
        help="Pair a new key and store it in ~/.nespresso_auth.json.",
        description=(
            "Generate an 8-byte pairing key and store it after the machine accepts it. "
            "Factory-reset the machine first if it is already paired, including to the phone app."
        ),
    )
    commands.add_parser(
        "stop",
        parents=[common, connect],
        help="Ask the machine to stop the current brew.",
        description="Send the stop frame. The machine ignores it when nothing is brewing.",
    )
    tui = commands.add_parser(
        "tui",
        parents=[common],
        help="Open the full-screen terminal UI.",
        description="Interactive terminal UI. Brew with the number keys, scan, pair, and read status.",
    )
    tui.add_argument(
        "--address",
        help="Bluetooth address to select. Defaults to the saved machine.",
    )
    return parser


def normalize_legacy_argv(argv: list[str]) -> list[str]:
    """Translate `brew.py espresso` into `nespresso brew espresso`."""
    if not argv:
        return argv
    head = argv[0]
    if head in DRINK_CHOICES or head in DRINK_ORDER:
        return ["brew", *argv]
    if head == "--scan":
        return ["scan", *argv[1:]]
    if head == "--reset-auth":
        return ["pair", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tui":
        from nespresso.tui import run_tui

        return run_tui(address=args.address)

    from nespresso.ble import (
        brew_drink,
        humanize_ble,
        pair_machine,
        read_status,
        scan_machines,
        stop_brew,
    )
    from nespresso.console import Console
    from nespresso.errors import NO_MACHINE

    console = Console(color=not args.no_color and not os.environ.get("NO_COLOR"))
    try:
        if args.command == "scan":
            return asyncio.run(_scan(args, console, scan_machines, NO_MACHINE))
        if args.command == "brew":
            asyncio.run(
                brew_drink(
                    args.address,
                    normalize_drink(args.drink),
                    args.temp,
                    console,
                    reset_auth=args.reset_auth,
                    prepare=not args.no_prepare,
                )
            )
            return 0
        if args.command == "status":
            report = asyncio.run(
                read_status(args.address, console, reset_auth=args.reset_auth)
            )
            console.table(report.rows)
            return 0
        if args.command == "pair":
            asyncio.run(pair_machine(args.address, console))
            return 0
        if args.command == "stop":
            asyncio.run(stop_brew(args.address, console))
            return 0
    except NespressoError as exc:
        console.error(str(exc))
        return 1
    except Exception as exc:
        from bleak.exc import BleakError

        if isinstance(exc, BleakError):
            console.error(humanize_ble(exc))
            return 1
        if isinstance(exc, (OSError, asyncio.TimeoutError)):
            console.error(humanize_ble(exc))
            return 1
        raise
    except KeyboardInterrupt:
        console.error("Cancelled.")
        return 130
    console.error(f"Unknown command {args.command}.")
    return 2


async def _scan(args, console, scan_machines, no_machine: str) -> int:
    console.progress("Scanning…")
    found = await scan_machines(timeout=args.timeout, include_all=args.all)
    if not found:
        console.error("No Bluetooth devices found. Is Bluetooth on?" if args.all else no_machine)
        return 1
    for machine in found:
        console.device(machine.address, machine.name, machine.rssi, machine.likely)
    likely = [machine for machine in found if machine.likely]
    noun = "machine" if len(likely) == 1 else "machines"
    console.success(f"{len(likely)} Nespresso {noun}.")
    return 0 if likely or args.all else 1


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number of seconds") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than 0")
    return number


if __name__ == "__main__":
    sys.exit(main())
