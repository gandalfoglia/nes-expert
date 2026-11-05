from __future__ import annotations

import contextlib
import io
import re
import unittest
from pathlib import Path

from nespresso.cli import build_parser, main, normalize_legacy_argv


class ParserTests(unittest.TestCase):
    def test_help_lists_the_commands(self) -> None:
        text = build_parser().format_help()
        for word in ("scan", "brew", "status", "pair", "stop", "tui"):
            self.assertIn(word, text)
        self.assertIn("nespresso brew espresso", text)
        self.assertIn("nespresso brew lungo --temp high", text)

    def test_brew_defaults(self) -> None:
        args = build_parser().parse_args(["brew", "espresso"])
        self.assertEqual(args.drink, "espresso")
        self.assertEqual(args.temp, "medium")
        self.assertFalse(args.reset_auth)
        self.assertFalse(args.no_prepare)

        hot = build_parser().parse_args(["brew", "lungo", "--temp", "high", "--reset-auth"])
        self.assertEqual(hot.temp, "high")
        self.assertTrue(hot.reset_auth)

        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                build_parser().parse_args(["brew", "latte"])

    def test_legacy_argv(self) -> None:
        self.assertEqual(normalize_legacy_argv(["espresso", "--temp", "low"]), ["brew", "espresso", "--temp", "low"])
        self.assertEqual(normalize_legacy_argv(["--scan"]), ["scan"])
        self.assertEqual(normalize_legacy_argv(["--reset-auth"]), ["pair", "--reset-auth"])

    def test_brew_help_exits_cleanly(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["brew", "--help"])
        self.assertEqual(caught.exception.code, 0)


class PublicTreeTests(unittest.TestCase):
    def test_no_saved_key_is_committed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r'"auth_key"\s*:\s*"[0-9a-fA-F]{16}"')
        checked = []
        for path in [root / "README.md", root / "brew.py", root / "pyproject.toml"]:
            checked.append(path)
        checked.extend((root / "src").rglob("*.py"))
        checked.extend((root / "tests").rglob("*.py"))
        for path in checked:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), path)
            self.assertNotIn(".nespresso_auth.json", path.name)


if __name__ == "__main__":
    unittest.main()
