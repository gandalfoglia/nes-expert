from __future__ import annotations

import unittest

from nespresso.ble import FoundMachine, choose_machine
from nespresso.protocol import (
    PREPARE,
    STOP,
    brew_payload,
    decode_machine_info,
    decode_pair_state,
    decode_serial,
    decode_slider,
    generate_auth_key,
    looks_like_machine,
    normalize_drink,
    response_problem,
    status_details,
    status_summary,
)


class PayloadTests(unittest.TestCase):
    def test_documented_frames(self) -> None:
        self.assertEqual(brew_payload("ristretto", "medium").hex(), "03050704000000000000")
        self.assertEqual(brew_payload("espresso", "low").hex(), "03050704000000000101")
        self.assertEqual(brew_payload("espresso", "medium").hex(), "03050704000000000001")
        self.assertEqual(brew_payload("lungo", "high").hex(), "03050704000000000202")
        self.assertEqual(brew_payload("hotwater", "low").hex(), "03050704000000000104")
        self.assertEqual(brew_payload("americano", "low").hex(), "03050704000000000105")
        self.assertEqual(PREPARE.hex(), "01100800000200c8000000")
        self.assertEqual(STOP.hex(), "03060102")

    def test_drink_aliases(self) -> None:
        self.assertEqual(normalize_drink("hot-water"), "hotwater")
        self.assertEqual(normalize_drink("Hot Water"), "hotwater")
        with self.assertRaises(ValueError):
            normalize_drink("latte")

    def test_auth_key_shape(self) -> None:
        seen = set()
        for _ in range(40):
            key = generate_auth_key()
            self.assertEqual(len(bytes.fromhex(key)), 8)
            self.assertEqual(key[0], "8")
            seen.add(key)
        self.assertGreater(len(seen), 1)


class StatusTests(unittest.TestCase):
    def test_idle_ready(self) -> None:
        raw = bytes.fromhex("400201e04000ffff")
        self.assertEqual(status_summary(raw), "ready")
        details = dict(status_details(raw))
        self.assertEqual(details["Water"], "ok")
        self.assertEqual(details["Pump"], "idle")
        self.assertEqual(details["Power"], "awake")

    def test_brewing_and_empty(self) -> None:
        self.assertIn("brewing", status_summary(bytes.fromhex("408401e04000ffff")))
        summary = status_summary(bytes.fromhex("418401e04000ffff"))
        self.assertIn("water tank empty", summary)
        self.assertIn("brewing", summary)

    def test_slider_serial_and_pair_state(self) -> None:
        self.assertEqual(decode_slider(b"\x02"), "closed")
        self.assertEqual(decode_slider(b"\x00"), "open")
        self.assertEqual(decode_serial(b"16069D70\x00extra"), "16069D70")
        self.assertIsNone(decode_serial(b"\x01\x02"))
        code, label = decode_pair_state(b"\x02")
        self.assertEqual(code, 2)
        self.assertIn("paired", label)
        self.assertEqual(decode_pair_state(None)[0], None)

    def test_machine_info(self) -> None:
        raw = bytes.fromhex("006c006d007d0076aabbccddeeff")
        rows = dict(decode_machine_info(raw))
        self.assertEqual(rows["Hardware"], "1.08")
        self.assertEqual(rows["Firmware"], "1.25")
        self.assertEqual(rows["MAC"], "AA:BB:CC:DD:EE:FF")

    def test_response_errors(self) -> None:
        self.assertIsNone(response_problem(bytes.fromhex("83050120")))
        self.assertIn("capsule", response_problem(bytes.fromhex("c305022412")).lower())
        self.assertIn("slider", response_problem(bytes.fromhex("c305022408")).lower())
        self.assertIn("tray", response_problem(bytes.fromhex("c305022403")).lower())
        self.assertIn("not brewing", response_problem(bytes.fromhex("c3060121")).lower())

    def test_name_filter(self) -> None:
        self.assertTrue(looks_like_machine("Expert&Milk_EXAMPLE", []))
        self.assertFalse(looks_like_machine("Headphones", []))
        self.assertTrue(
            looks_like_machine("", ["06aa1910-f22a-11e3-9daa-0002a5d5c51b"])
        )


class ChooseTests(unittest.TestCase):
    def test_prefers_saved_then_strongest(self) -> None:
        weak = FoundMachine("A", "Expert one", -80, True)
        strong = FoundMachine("B", "Expert two", -40, True)
        other = FoundMachine("C", "Phone", -20, False)
        self.assertEqual(choose_machine([weak, strong, other]).address, "B")
        self.assertEqual(choose_machine([weak, strong], saved_address="A").address, "A")
        self.assertIsNone(choose_machine([other]))


if __name__ == "__main__":
    unittest.main()
