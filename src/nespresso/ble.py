"""Bluetooth session for an Expert & Milk machine."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Optional, Protocol

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason, BleakError

from nespresso.authstore import AuthRecord, key_for_address, load_auth, save_auth
from nespresso.errors import (
    ALREADY_PAIRED,
    BIND_FAILED,
    NO_MACHINE,
    UNKNOWN_KEY,
    AuthError,
    BrewRejected,
    MachineNotFound,
    NespressoError,
)
from nespresso.protocol import (
    CHAR_AUTH,
    CHAR_COMMAND,
    CHAR_INFO,
    CHAR_PAIR_STATE,
    CHAR_RESPONSE,
    CHAR_SERIAL,
    CHAR_SLIDER,
    CHAR_STATUS,
    CHAR_TX_LEVEL,
    PREPARE,
    STOP,
    brew_payload,
    decode_machine_info,
    decode_pair_state,
    decode_serial,
    decode_slider,
    drink_label,
    generate_auth_key,
    looks_like_machine,
    response_problem,
    status_details,
    status_summary,
)


class Reporter(Protocol):
    def progress(self, message: str) -> None: ...

    def info(self, message: str) -> None: ...

    def success(self, message: str) -> None: ...

    def warn(self, message: str) -> None: ...


@dataclass(frozen=True)
class FoundMachine:
    address: str
    name: str
    rssi: Optional[int]
    likely: bool
    device: Optional[BLEDevice] = None


@dataclass
class StatusReport:
    summary: str
    rows: list[tuple[str, str]]


def humanize_ble(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "Timed out talking to the machine."
    if isinstance(exc, BleakBluetoothNotAvailableError):
        reason = exc.reason
        if reason is BleakBluetoothNotAvailableReason.POWERED_OFF:
            return "Bluetooth is off. Turn it on in System Settings and try again."
        if reason in (
            BleakBluetoothNotAvailableReason.DENIED_BY_USER,
            BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
            BleakBluetoothNotAvailableReason.DENIED_BY_UNKNOWN,
        ):
            return (
                "Bluetooth permission was denied. Allow Python or Terminal in "
                "System Settings → Privacy & Security → Bluetooth, then try again."
            )
        if reason is BleakBluetoothNotAvailableReason.NO_BLUETOOTH:
            return "This Mac has no Bluetooth radio the app can use."
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "powered off" in lowered or "turned off" in lowered:
        return "Bluetooth is off. Turn it on in System Settings and try again."
    if "not found" in lowered or "disconnected" in lowered:
        return (
            "The machine dropped the connection. Keep it close, quit the Nespresso app, "
            "and try again."
        )
    return text


def choose_machine(
    found: list[FoundMachine], saved_address: Optional[str] = None
) -> Optional[FoundMachine]:
    if saved_address:
        for machine in found:
            if machine.address.upper() == saved_address.upper():
                return machine
    likely = [machine for machine in found if machine.likely]
    if not likely:
        return None
    return max(likely, key=lambda machine: machine.rssi if machine.rssi is not None else -999)


async def scan_machines(timeout: float = 8.0, include_all: bool = False) -> list[FoundMachine]:
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found: list[FoundMachine] = []
    for device, advertisement in discovered.values():
        name = advertisement.local_name or device.name or ""
        likely = looks_like_machine(name, list(advertisement.service_uuids or []))
        if not include_all and not likely:
            continue
        found.append(
            FoundMachine(
                address=device.address,
                name=name,
                rssi=advertisement.rssi,
                likely=likely,
                device=device,
            )
        )
    found.sort(key=lambda machine: (not machine.likely, -(machine.rssi or -999), machine.name))
    return found


class Session:
    def __init__(self, client: BleakClient, address: str, name: str, reporter: Reporter) -> None:
        self.client = client
        self.address = address
        self.name = name
        self.reporter = reporter

    async def authenticate(self, reset_auth: bool) -> None:
        saved = key_for_address(self.address)
        state, label = await self._pair_state()
        self.reporter.info(f"Pair state: {label}")

        if reset_auth:
            if state == 2:
                raise AuthError(ALREADY_PAIRED)
            await self._bind_new_key()
            return

        if saved is not None:
            await self._write(CHAR_AUTH, bytes.fromhex(saved.auth_key))
            await asyncio.sleep(0.2)
            if state != 2:
                confirmed, _confirmed_label = await self._pair_state()
                if confirmed not in (2, None):
                    await self._bind_key(saved.auth_key, replace=False)
            if self.name and self.name != saved.name:
                save_auth(
                    AuthRecord(
                        address=self.address,
                        name=self.name,
                        auth_key=saved.auth_key,
                        paired_at=saved.paired_at,
                    )
                )
            self.reporter.info("Authenticated.")
            return

        if state == 2:
            raise AuthError(UNKNOWN_KEY)
        await self._bind_new_key()

    async def send_brew(self, drink: str, temp: str, prepare: bool) -> None:
        label = drink_label(drink).lower()
        if prepare:
            try:
                response = await self.command(PREPARE)
            except BleakError as exc:
                self.reporter.warn(f"Prepare step skipped ({exc}).")
            else:
                problem = response_problem(response)
                if problem:
                    self.reporter.warn(problem)
            await asyncio.sleep(0.2)
        self.reporter.progress(f"Brewing {label}…")
        response = await self.command(brew_payload(drink, temp))
        problem = response_problem(response)
        if problem:
            raise BrewRejected(problem)
        if response is None:
            self.reporter.success(f"Brew command sent. {label.capitalize()} should start shortly.")
        else:
            self.reporter.success(f"{label.capitalize()} is brewing.")

    async def stop(self) -> None:
        self.reporter.progress("Stopping…")
        response = await self.command(STOP)
        problem = response_problem(response)
        if problem:
            self.reporter.warn(problem)
            return
        self.reporter.success("Stop command sent.")

    async def read_status(self) -> StatusReport:
        rows: list[tuple[str, str]] = []
        if self.name:
            rows.append(("Name", self.name))
        rows.append(("Address", self.address))
        _code, pair_label = await self._pair_state()
        rows.append(("Pairing", pair_label))
        raw_status = await self._read_optional(CHAR_STATUS)
        summary = status_summary(raw_status)
        rows.append(("State", summary))
        rows.extend(status_details(raw_status))
        slider = decode_slider(await self._read_optional(CHAR_SLIDER))
        if slider:
            rows.append(("Slider", slider))
        serial = decode_serial(await self._read_optional(CHAR_SERIAL))
        if serial:
            rows.append(("Serial", serial))
        rows.extend(decode_machine_info(await self._read_optional(CHAR_INFO)))
        if raw_status:
            rows.append(("Status bytes", raw_status.hex(" ")))
        self.reporter.success(summary.capitalize() if summary != "unavailable" else "Status read.")
        return StatusReport(summary=summary, rows=rows)

    async def command(self, payload: bytes) -> Optional[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        armed = False

        def on_notify(_sender: object, data: bytearray) -> None:
            if not armed:
                return
            loop.call_soon_threadsafe(queue.put_nowait, bytes(data))

        subscribed = False
        try:
            try:
                await self.client.start_notify(CHAR_RESPONSE, on_notify)
                subscribed = True
            except BleakError as exc:
                self.reporter.warn(f"No command response channel ({exc}).")
            armed = True
            await self._write(CHAR_COMMAND, payload)
            if not subscribed:
                await asyncio.sleep(0.3)
                return None
            try:
                return await asyncio.wait_for(queue.get(), 2.5)
            except asyncio.TimeoutError:
                return None
        finally:
            if subscribed:
                with contextlib.suppress(BleakError, OSError):
                    await self.client.stop_notify(CHAR_RESPONSE)

    async def _bind_new_key(self) -> None:
        await self._bind_key(generate_auth_key(), replace=True)

    async def _bind_key(self, key: str, replace: bool) -> None:
        self.reporter.progress("Pairing…")
        await self._write(CHAR_TX_LEVEL, b"\x01")
        await asyncio.sleep(0.3)
        await self._write(CHAR_AUTH, bytes.fromhex(key))
        await asyncio.sleep(0.5)
        state: Optional[int] = None
        for _attempt in range(3):
            state, label = await self._pair_state()
            if state == 2:
                saved = key_for_address(self.address)
                paired_at = "" if replace or saved is None else saved.paired_at
                save_auth(
                    AuthRecord(
                        address=self.address,
                        name=self.name,
                        auth_key=key,
                        paired_at=paired_at,
                    )
                )
                self.reporter.success("Paired. The key is stored in ~/.nespresso_auth.json.")
                return
            if state is None:
                self.reporter.warn(f"Pair state: {label}")
            await asyncio.sleep(0.4)
        if state is None:
            save_auth(AuthRecord(address=self.address, name=self.name, auth_key=key))
            self.reporter.warn(
                "The key was saved, but the machine did not confirm pair state. Run `nespresso status`."
            )
            return
        raise AuthError(BIND_FAILED)

    async def _pair_state(self) -> tuple[Optional[int], str]:
        data = await self._read_optional(CHAR_PAIR_STATE)
        return decode_pair_state(data)

    async def _read_optional(self, uuid: str) -> Optional[bytes]:
        try:
            return bytes(await self.client.read_gatt_char(uuid))
        except (BleakError, OSError, asyncio.TimeoutError):
            return None

    async def _write(self, uuid: str, data: bytes) -> None:
        try:
            await self.client.write_gatt_char(uuid, data, response=True)
        except BleakError:
            await self.client.write_gatt_char(uuid, data, response=False)


async def _open(address: Optional[str], reporter: Reporter, pick_when_many: bool) -> tuple[BleakClient, str, str]:
    saved = load_auth()
    name = ""
    device: Optional[BLEDevice] = None
    if address is None and saved is not None:
        address = saved.address
        name = saved.name
        reporter.info(f"Using {name or address}")
    if address is None:
        reporter.progress("Scanning…")
        found = await scan_machines(timeout=8.0)
        likely = [machine for machine in found if machine.likely]
        if not likely:
            raise MachineNotFound(NO_MACHINE)
        if len(likely) > 1 and not pick_when_many:
            listing = "\n".join(
                f"  {machine.address}   {machine.name or '(no name)'}" for machine in likely
            )
            raise MachineNotFound(
                "More than one Nespresso machine is nearby. Pass --address.\n" + listing
            )
        chosen = choose_machine(found)
        if chosen is None:
            raise MachineNotFound(NO_MACHINE)
        reporter.info(f"Found {chosen.name or chosen.address}")
        reporter.progress("Connecting…")
        device = chosen.device
        if device is None:
            device = await BleakScanner.find_device_by_address(chosen.address, timeout=8.0)
        address = chosen.address
        name = chosen.name
    else:
        reporter.progress("Connecting…")
        device = await BleakScanner.find_device_by_address(address, timeout=12.0)
    if device is None:
        raise MachineNotFound(
            f"Could not find {address}. Turn the machine on, stand close, and quit the Nespresso app."
        )
    if device.name:
        name = device.name
    client = BleakClient(device, timeout=20.0)
    try:
        await client.connect()
    except BleakError:
        with contextlib.suppress(BleakError, OSError):
            await client.disconnect()
        raise
    if not client.is_connected:
        raise MachineNotFound("The machine accepted the connection, then dropped it.")
    return client, device.address, name


async def _with_machine(address: Optional[str], reporter: Reporter, action, pick_when_many: bool = False):
    client, resolved, name = await _open(address, reporter, pick_when_many)
    session = Session(client, resolved, name, reporter)
    try:
        return await action(session)
    finally:
        with contextlib.suppress(BleakError, OSError):
            if client.is_connected:
                await client.disconnect()


async def brew_drink(
    address: Optional[str],
    drink: str,
    temp: str,
    reporter: Reporter,
    reset_auth: bool = False,
    prepare: bool = True,
    pick_when_many: bool = False,
) -> None:
    async def action(session: Session) -> None:
        await session.authenticate(reset_auth)
        await session.send_brew(drink, temp, prepare)

    await _with_machine(address, reporter, action, pick_when_many)


async def read_status(
    address: Optional[str],
    reporter: Reporter,
    reset_auth: bool = False,
    pick_when_many: bool = False,
) -> StatusReport:
    async def action(session: Session) -> StatusReport:
        try:
            await session.authenticate(reset_auth)
        except NespressoError as exc:
            reporter.warn(str(exc))
        return await session.read_status()

    return await _with_machine(address, reporter, action, pick_when_many)


async def pair_machine(
    address: Optional[str],
    reporter: Reporter,
    pick_when_many: bool = False,
) -> None:
    async def action(session: Session) -> None:
        await session.authenticate(reset_auth=True)

    await _with_machine(address, reporter, action, pick_when_many)


async def stop_brew(
    address: Optional[str],
    reporter: Reporter,
    pick_when_many: bool = False,
) -> None:
    async def action(session: Session) -> None:
        await session.authenticate(reset_auth=False)
        await session.stop()

    await _with_machine(address, reporter, action, pick_when_many)
