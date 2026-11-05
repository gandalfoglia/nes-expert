from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from nespresso.authstore import AuthRecord, load_auth, save_auth
from nespresso.errors import NespressoError


class AuthStoreTests(unittest.TestCase):
    def test_round_trip_is_private_and_hides_nothing_in_the_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".nespresso_auth.json"
            save_auth(
                AuthRecord(
                    address="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                    name="Expert&Milk_EXAMPLE",
                    auth_key="8abc1234deadbeef",
                ),
                path,
            )
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            loaded = load_auth(path)
            assert loaded is not None
            self.assertEqual(loaded.auth_key, "8abc1234deadbeef")
            self.assertEqual(loaded.name, "Expert&Milk_EXAMPLE")
            self.assertTrue(loaded.paired_at)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(body), {"address", "name", "auth_key", "paired_at"})

    def test_missing_file_and_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertIsNone(load_auth(path))
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(NespressoError):
                load_auth(path)

    def test_rejects_a_short_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(
                json.dumps({"address": "AA", "auth_key": "1234"}),
                encoding="utf-8",
            )
            with self.assertRaises(NespressoError):
                load_auth(path)


if __name__ == "__main__":
    unittest.main()
