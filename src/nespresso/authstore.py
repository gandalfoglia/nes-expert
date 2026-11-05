"""Persist the machine address and pairing key outside the repository."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nespresso.errors import NespressoError
from nespresso.protocol import parse_auth_key

DEFAULT_PATH = Path.home() / ".nespresso_auth.json"


@dataclass
class AuthRecord:
    address: str
    name: str
    auth_key: str
    paired_at: str = ""


def load_auth(path: Optional[Path] = None) -> Optional[AuthRecord]:
    file_path = path or DEFAULT_PATH
    if not file_path.exists():
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NespressoError(
            f"Could not read {file_path}. Fix the file or delete it and pair again."
        ) from exc
    if not isinstance(payload, dict):
        raise NespressoError(f"{file_path} is not a JSON object.")
    address = str(payload.get("address") or "").strip()
    key = str(payload.get("auth_key") or "").strip()
    if not address or not key:
        raise NespressoError(f"{file_path} is missing an address or auth key.")
    try:
        key = parse_auth_key(key)
    except ValueError as exc:
        raise NespressoError(f"{file_path} has an invalid auth key. {exc}") from exc
    return AuthRecord(
        address=address,
        name=str(payload.get("name") or "").strip(),
        auth_key=key,
        paired_at=str(payload.get("paired_at") or ""),
    )


def save_auth(record: AuthRecord, path: Optional[Path] = None) -> None:
    """Write the key with owner-only permissions. The key is never printed."""
    file_path = path or DEFAULT_PATH
    file_path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "address": record.address,
        "name": record.name,
        "auth_key": parse_auth_key(record.auth_key),
        "paired_at": record.paired_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(file_path)
    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)


def key_for_address(address: str, path: Optional[Path] = None) -> Optional[AuthRecord]:
    saved = load_auth(path)
    if saved is None or saved.address.upper() != address.upper():
        return None
    return saved
