"""Expert & Milk BLE identifiers and frame encoding.

Byte layouts follow the public notes for the Original-line machines
(Expert, Expert & Milk, Prodigio). Status bits are best-effort: the raw
bytes are always shown alongside the decoded fields.
"""

from __future__ import annotations

import os
from typing import Optional

AUTH_SERVICE = "06aa1910-f22a-11e3-9daa-0002a5d5c51b"
CMD_SERVICE = "06aa1920-f22a-11e3-9daa-0002a5d5c51b"

CHAR_INFO = "06aa3a21-f22a-11e3-9daa-0002a5d5c51b"
CHAR_SERIAL = "06aa3a31-f22a-11e3-9daa-0002a5d5c51b"
CHAR_AUTH = "06aa3a41-f22a-11e3-9daa-0002a5d5c51b"
CHAR_PAIR_STATE = "06aa3a51-f22a-11e3-9daa-0002a5d5c51b"
CHAR_TX_LEVEL = "06aa3a61-f22a-11e3-9daa-0002a5d5c51b"
CHAR_STATUS = "06aa3a12-f22a-11e3-9daa-0002a5d5c51b"
CHAR_SLIDER = "06aa3a22-f22a-11e3-9daa-0002a5d5c51b"
CHAR_COMMAND = "06aa3a42-f22a-11e3-9daa-0002a5d5c51b"
CHAR_RESPONSE = "06aa3a52-f22a-11e3-9daa-0002a5d5c51b"

# Last byte of the brew frame. hotwater is 0x04, americano is 0x05.
DRINKS = {
    "ristretto": 0x00,
    "espresso": 0x01,
    "lungo": 0x02,
    "hotwater": 0x04,
    "americano": 0x05,
}

DRINK_LABELS = {
    "ristretto": "Ristretto",
    "espresso": "Espresso",
    "lungo": "Lungo",
    "hotwater": "Hot water",
    "americano": "Americano",
}

DRINK_ORDER = ["ristretto", "espresso", "lungo", "americano", "hotwater"]

# Second-to-last byte. Medium is 0x00, not 0x01.
TEMPS = {
    "low": 0x01,
    "medium": 0x00,
    "high": 0x02,
}

TEMP_ORDER = ["low", "medium", "high"]

DRINK_CHOICES = DRINK_ORDER + ["hot-water"]

# Seen before a successful brew on Expert. Safe to skip if the write fails.
PREPARE = bytes.fromhex("01100800000200c8000000")
STOP = bytes.fromhex("03060102")

PAIR_STATE_NAMES = {
    0: "not paired",
    1: "temporary",
    2: "paired",
    3: "undefined",
}

_RESPONSE_REASONS = (
    (bytes.fromhex("2408"), "The slider is open. Close it, then brew again."),
    (bytes.fromhex("2412"), "Open the slider, insert a capsule, and close it before brewing."),
    (bytes.fromhex("2403"), "The drip tray is full or not seated."),
    (bytes.fromhex("2360"), "Open the slider, insert a capsule, and close it before brewing."),
    (bytes.fromhex("3601"), "The machine rejected this drink."),
    (bytes.fromhex("3603"), "The machine rejected the command."),
)

NAME_HINTS = ("nespresso", "expert", "prodigio", "aeroccino", "inissia", "lattissima")


def normalize_drink(name: str) -> str:
    key = name.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if key not in DRINKS:
        known = ", ".join(DRINK_ORDER)
        raise ValueError(f"Unknown drink {name!r}. Choose one of: {known}.")
    return key


def drink_label(name: str) -> str:
    return DRINK_LABELS[normalize_drink(name)]


def brew_payload(drink: str, temp: str) -> bytes:
    """Build 03 05 07 04 00 00 00 00 <temp> <drink>."""
    drink_name = normalize_drink(drink)
    temp_name = temp.strip().lower()
    if temp_name not in TEMPS:
        raise ValueError("Temperature must be low, medium, or high.")
    return bytes(
        [0x03, 0x05, 0x07, 0x04, 0x00, 0x00, 0x00, 0x00, TEMPS[temp_name], DRINKS[drink_name]]
    )


def generate_auth_key() -> str:
    """8 bytes whose first hex digit is 8, matching the machine's key format."""
    raw = bytearray(os.urandom(8))
    raw[0] = 0x80 | (raw[0] & 0x0F)
    return raw.hex()


def parse_auth_key(value: str) -> str:
    text = value.strip().lower()
    if len(text) != 16 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError("Auth key must be 16 hex characters (8 bytes).")
    return text


def looks_like_machine(name: str, service_uuids: list[str]) -> bool:
    blob = (name or "").lower()
    if any(hint in blob for hint in NAME_HINTS):
        return True
    wanted = {AUTH_SERVICE, CMD_SERVICE}
    return any(uuid.lower() in wanted for uuid in service_uuids)


def decode_pair_state(data: Optional[bytes]) -> tuple[Optional[int], str]:
    if not data:
        return None, "unreadable"
    code = data[0]
    return code, f"{PAIR_STATE_NAMES.get(code, 'unknown')} ({code})"


def status_summary(data: Optional[bytes]) -> str:
    if not data or len(data) < 2:
        return "unavailable"
    byte0, byte1 = data[0], data[1]
    parts = []
    if byte0 & 0x01:
        parts.append("water tank empty")
    if byte0 & 0x04:
        parts.append("descaling needed")
    if byte0 & 0x10:
        parts.append("capsule mechanism jammed")
    if byte1 & 0x40 or byte1 & 0x10:
        parts.append("check the drip tray")
    if byte1 & 0x80 and byte1 & 0x04:
        parts.append("brewing")
    elif byte1 & 0x04:
        parts.append("pump running")
    elif byte1 & 0x08:
        parts.append("asleep")
    elif byte1 & 0x01:
        parts.append("warming up")
    elif byte1 & 0x02:
        parts.append("ready")
    else:
        parts.append("idle")
    return ", ".join(parts)


def status_details(data: Optional[bytes]) -> list[tuple[str, str]]:
    if not data or len(data) < 2:
        return []
    byte0, byte1 = data[0], data[1]
    if byte1 & 0x08:
        power = "asleep"
    elif byte1 & 0x01:
        power = "warming"
    else:
        power = "awake"
    return [
        ("Water", "empty" if byte0 & 0x01 else "ok"),
        ("Descaling", "needed" if byte0 & 0x04 else "ok"),
        ("Mechanism", "jammed" if byte0 & 0x10 else "ok"),
        ("Capsule", "engaged" if byte1 & 0x80 else "not engaged"),
        ("Pump", "running" if byte1 & 0x04 else "idle"),
        ("Power", power),
        ("Tray", "check" if (byte1 & 0x40 or byte1 & 0x10) else "ok"),
    ]


def decode_slider(data: Optional[bytes]) -> Optional[str]:
    if not data:
        return None
    if data[0] == 0x00:
        return "open"
    if data[0] == 0x02:
        return "closed"
    return f"unknown ({data[0]:02x})"


def decode_serial(data: Optional[bytes]) -> Optional[str]:
    if not data:
        return None
    text = data.split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
    if len(text) < 4 or not text.isprintable() or not text.isascii():
        return None
    return text


def _version(data: bytes, offset: int) -> Optional[str]:
    if len(data) < offset + 2:
        return None
    value = int.from_bytes(data[offset : offset + 2], "big")
    return f"{value // 100}.{value % 100:02d}"


def decode_machine_info(data: Optional[bytes]) -> list[tuple[str, str]]:
    if not data or len(data) < 8:
        return []
    rows = []
    for label, offset in (
        ("Hardware", 0),
        ("Bootloader", 2),
        ("Firmware", 4),
        ("Bluetooth", 6),
    ):
        version = _version(data, offset)
        if version is not None:
            rows.append((label, version))
    if len(data) >= 14:
        mac = data[8:14]
        if any(mac):
            rows.append(("MAC", ":".join(f"{byte:02X}" for byte in mac)))
    return rows


def response_problem(data: Optional[bytes]) -> Optional[str]:
    """Return a human message when a command response has the error bit set."""
    if not data or not (data[0] & 0x40):
        return None
    if len(data) >= 2 and data[0] == 0xC3 and data[1] == 0x06:
        return "The machine was not brewing."
    for signature, message in _RESPONSE_REASONS:
        if signature in data:
            return message
    return f"The machine refused the command (response {data.hex()})."
