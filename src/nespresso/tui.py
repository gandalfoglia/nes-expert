"""Full-screen terminal UI. BLE stays on the asyncio loop that curses polls."""

from __future__ import annotations

import asyncio
import contextlib
import curses
import sys
from typing import Callable, Optional

from nespresso.authstore import load_auth
from nespresso.errors import NespressoError
from nespresso.protocol import DRINK_ORDER, TEMP_ORDER, drink_label

_HAVE_COLOR = False


class TuiState:
    def __init__(self, address: Optional[str] = None) -> None:
        self.address = address
        self.name = ""
        self.temp_index = 1
        self.drink_index = 1
        self.machines: list = []
        self.machine_index = 0
        self.events: list[tuple[str, str]] = []
        self.status_rows: list[tuple[str, str]] = []
        self.summary = "not connected"
        self.busy = False
        self.quit = False
        self.dirty = True
        self.confirm: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        saved = None
        try:
            saved = load_auth()
        except NespressoError as exc:
            self.warn(str(exc))
        if saved is not None and not self.address:
            self.address = saved.address
            self.name = saved.name
            self.info(f"Saved machine {saved.name or saved.address}")
        elif saved is not None and saved.address.upper() == (self.address or "").upper():
            self.name = saved.name

    def progress(self, message: str) -> None:
        self._add("progress", message)

    def info(self, message: str) -> None:
        self._add("info", message)

    def success(self, message: str) -> None:
        self._add("success", message)
        if message:
            self.summary = message

    def warn(self, message: str) -> None:
        self._add("warn", message)

    def _add(self, level: str, message: str) -> None:
        for line in str(message).splitlines() or [""]:
            while len(line) > 78:
                self.events.append((level, line[:78]))
                line = line[78:]
            self.events.append((level, line))
        del self.events[:-100]
        self.dirty = True


def run_tui(address: Optional[str] = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("The TUI needs an interactive terminal.", file=sys.stderr)
        return 1
    try:
        return curses.wrapper(lambda stdscr: asyncio.run(_run(stdscr, address)))
    except KeyboardInterrupt:
        return 130


async def _run(stdscr, address: Optional[str]) -> int:
    _setup_colors()
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    state = TuiState(address)
    while not state.quit:
        if state.dirty:
            _draw(stdscr, state)
            state.dirty = False
        key = stdscr.getch()
        if key != -1:
            await _on_key(state, key)
        await asyncio.sleep(0.05)
    if state.task is not None and not state.task.done():
        state.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.task
    return 0


async def _on_key(state: TuiState, key: int) -> None:
    if state.confirm == "pair":
        if key in (ord("q"), ord("Q")):
            state.quit = True
        elif key in (ord("y"), ord("Y")):
            state.confirm = None
            _start(state, lambda: _pair(state))
        elif key in (ord("n"), ord("N"), 27):
            state.confirm = None
            state.info("Pairing cancelled.")
        return

    if key in (ord("q"), ord("Q")):
        if state.task is not None and not state.task.done():
            state.task.cancel()
        state.quit = True
        return
    if key == curses.KEY_RESIZE:
        state.dirty = True
        return
    if state.busy:
        return

    if key in (curses.KEY_UP, ord("k")):
        state.drink_index = (state.drink_index - 1) % len(DRINK_ORDER)
        state.dirty = True
    elif key in (curses.KEY_DOWN, ord("j")):
        state.drink_index = (state.drink_index + 1) % len(DRINK_ORDER)
        state.dirty = True
    elif key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("t"), ord("T")):
        step = -1 if key == curses.KEY_LEFT else 1
        state.temp_index = (state.temp_index + step) % len(TEMP_ORDER)
        state.dirty = True
    elif key in (ord("["), ord("]")) and state.machines:
        step = -1 if key == ord("[") else 1
        state.machine_index = (state.machine_index + step) % len(state.machines)
        machine = state.machines[state.machine_index]
        state.address = machine.address
        state.name = machine.name
        state.info(f"Selected {machine.name or machine.address}")
    elif key in (10, 13, curses.KEY_ENTER):
        _start(state, lambda: _brew(state, DRINK_ORDER[state.drink_index]))
    elif ord("1") <= key <= ord("5"):
        drink = DRINK_ORDER[key - ord("1")]
        _start(state, lambda drink=drink: _brew(state, drink))
    elif key in (ord("s"), ord("S")):
        _start(state, lambda: _scan(state))
    elif key in (ord("u"), ord("U")):
        _start(state, lambda: _status(state))
    elif key in (ord("p"), ord("P")):
        state.confirm = "pair"
        state.warn("Factory-reset first if the phone app is paired. Press y to pair, n to cancel.")
    elif key in (ord("x"), ord("X")):
        _start(state, lambda: _stop(state))


def _start(state: TuiState, work: Callable[[], asyncio.Future]) -> None:
    if state.busy:
        state.warn("Already working…")
        return
    state.busy = True
    state.dirty = True

    async def runner() -> None:
        try:
            await work()
        except asyncio.CancelledError:
            state.warn("Cancelled.")
            raise
        except NespressoError as exc:
            state.warn(str(exc))
        except Exception as exc:
            from nespresso.ble import humanize_ble

            state.warn(humanize_ble(exc))
        finally:
            state.busy = False
            state.dirty = True

    state.task = asyncio.create_task(runner())


async def _scan(state: TuiState) -> None:
    from nespresso.ble import choose_machine, scan_machines
    from nespresso.errors import NO_MACHINE

    state.progress("Scanning…")
    found = await scan_machines(timeout=8.0)
    state.machines = found
    if not found:
        state.warn(NO_MACHINE)
        return
    chosen = choose_machine(found, state.address)
    if chosen is not None:
        state.machine_index = found.index(chosen)
        state.address = chosen.address
        state.name = chosen.name
    for machine in found:
        mark = "machine" if machine.likely else "device"
        signal = f", {machine.rssi} dBm" if machine.rssi is not None else ""
        state.info(f"{machine.name or '(no name)'} ({mark}{signal})")
        state.info(machine.address)
    if len(found) > 1:
        state.info("Several devices. Use [ and ] to choose.")


async def _brew(state: TuiState, drink: str) -> None:
    from nespresso.ble import brew_drink

    await brew_drink(
        state.address,
        drink,
        TEMP_ORDER[state.temp_index],
        state,
        prepare=True,
        pick_when_many=True,
    )


async def _status(state: TuiState) -> None:
    from nespresso.ble import read_status

    report = await read_status(state.address, state, pick_when_many=True)
    state.status_rows = report.rows
    state.summary = report.summary
    state.dirty = True
    for label, value in report.rows:
        if label == "Address":
            state.address = value
        elif label == "Name":
            state.name = value


async def _pair(state: TuiState) -> None:
    from nespresso.ble import pair_machine

    await pair_machine(state.address, state, pick_when_many=True)


async def _stop(state: TuiState) -> None:
    from nespresso.ble import stop_brew

    await stop_brew(state.address, state, pick_when_many=True)


def _setup_colors() -> None:
    global _HAVE_COLOR
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    _HAVE_COLOR = True


def _attr(pair: int, bold: bool = False) -> int:
    if not _HAVE_COLOR:
        return curses.A_BOLD if bold else curses.A_NORMAL
    value = curses.color_pair(pair)
    if bold:
        value |= curses.A_BOLD
    return value


def _put(stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = stdscr.getmaxyx()
    if y < 0 or y >= height or x >= width - 1:
        return
    try:
        stdscr.addnstr(y, x, text, max(1, width - x - 1), attr)
    except curses.error:
        return


def _draw(stdscr, state: TuiState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < 20 or width < 52:
        _put(stdscr, 0, 0, "Make the terminal at least 52×20.")
        stdscr.refresh()
        return

    _put(stdscr, 0, 2, "nespresso", _attr(1, bold=True))
    _put(stdscr, 0, 14, "expert & milk", _attr(4))
    machine = state.name or "no machine yet"
    _put(stdscr, 1, 2, machine, _attr(1, bold=True) if state.name else curses.A_DIM)
    address = state.address or "press s to scan"
    _put(stdscr, 2, 2, address, curses.A_DIM)
    summary = "working…" if state.busy else state.summary
    _put(stdscr, 3, 2, summary, _attr(2) if not state.busy else _attr(4))

    _put(stdscr, 5, 2, "Temperature", curses.A_DIM)
    temp_bits = []
    for index, name in enumerate(TEMP_ORDER):
        temp_bits.append(name.upper() if index == state.temp_index else name)
    _put(stdscr, 5, 16, "  ".join(temp_bits), _attr(1, bold=True))

    _put(stdscr, 7, 2, "Drinks", curses.A_DIM)
    for index, drink in enumerate(DRINK_ORDER):
        row = 8 + index
        selected = index == state.drink_index
        label = f" {index + 1}  {drink_label(drink)}"
        if selected:
            _put(stdscr, row, 2, label.ljust(24), _attr(5, bold=True))
        else:
            _put(stdscr, row, 2, label, curses.A_NORMAL)

    detail_top = 8
    detail_left = 30
    if state.status_rows:
        for offset, (label, value) in enumerate(state.status_rows[:5]):
            _put(stdscr, detail_top + offset, detail_left, f"{label}", curses.A_DIM)
            _put(stdscr, detail_top + offset, detail_left + 14, value)
    else:
        _put(stdscr, detail_top, detail_left, "Status appears here", curses.A_DIM)
        _put(stdscr, detail_top + 1, detail_left, "after you press u.", curses.A_DIM)

    log_top = 14
    _put(stdscr, log_top, 2, "Activity", curses.A_DIM)
    visible = height - log_top - 3
    events = state.events[-visible:]
    for offset, (level, line) in enumerate(events):
        attr = {
            "progress": _attr(4),
            "success": _attr(2),
            "warn": _attr(3),
            "error": _attr(3),
        }.get(level, curses.A_NORMAL)
        _put(stdscr, log_top + 1 + offset, 2, line, attr)

    if state.confirm == "pair":
        footer = "y pair a new key    n cancel"
    elif state.busy:
        footer = "working…    q cancel"
    else:
        footer = "1-5 brew   enter brew   t temp   s scan   u status   p pair   x stop   q quit"
    _put(stdscr, height - 1, 1, footer[: width - 2], curses.A_DIM)
    stdscr.refresh()
