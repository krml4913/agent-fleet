"""Tests for ``fleet.simple_yaml`` — flat ``key: value`` reader/writer."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import simple_yaml  # noqa: E402
from fleet import state  # noqa: E402
from fleet.commands.done import _truthy  # noqa: E402


class LoadBasicsTests(unittest.TestCase):
    def test_plain_value(self) -> None:
        self.assertEqual(simple_yaml.load("k: v\n"), {"k": "v"})

    def test_full_line_comment_skipped(self) -> None:
        text = "# a comment\nk: v\n  # indented comment\n"
        self.assertEqual(simple_yaml.load(text), {"k": "v"})

    def test_blank_lines_skipped(self) -> None:
        self.assertEqual(simple_yaml.load("\nk: v\n\n"), {"k": "v"})

    def test_missing_colon_raises(self) -> None:
        with self.assertRaises(ValueError):
            simple_yaml.load("no colon here\n")

    def test_empty_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            simple_yaml.load(": v\n")


class InlineCommentTests(unittest.TestCase):
    def test_strips_trailing_inline_comment(self) -> None:
        self.assertEqual(simple_yaml.load("k: true  # note\n"), {"k": "true"})

    def test_strips_with_single_leading_space(self) -> None:
        self.assertEqual(simple_yaml.load("k: foo # bar\n"), {"k": "foo"})

    def test_hash_glued_to_token_is_kept(self) -> None:
        # No preceding whitespace -> not a comment start.
        self.assertEqual(simple_yaml.load("k: foo#bar\n"), {"k": "foo#bar"})

    def test_value_ending_in_hash_word_kept(self) -> None:
        # A legit value with no space-`#` is unchanged.
        self.assertEqual(simple_yaml.load("k: c#3\n"), {"k": "c#3"})

    def test_no_comment_value_unchanged(self) -> None:
        self.assertEqual(
            simple_yaml.load("k: hello world\n"), {"k": "hello world"}
        )

    def test_comment_only_after_value_yields_value(self) -> None:
        self.assertEqual(
            simple_yaml.load("k: a b  # c d\n"), {"k": "a b"}
        )


class QuotedValueTests(unittest.TestCase):
    def test_double_quoted_interior_hash_preserved(self) -> None:
        self.assertEqual(simple_yaml.load('k: "a#b"\n'), {"k": "a#b"})

    def test_single_quoted_interior_hash_preserved(self) -> None:
        self.assertEqual(simple_yaml.load("k: 'a#b'\n"), {"k": "a#b"})

    def test_quoted_space_hash_preserved(self) -> None:
        self.assertEqual(simple_yaml.load('k: "x  # y"\n'), {"k": "x  # y"})

    def test_quotes_stripped_from_plain_quoted_value(self) -> None:
        self.assertEqual(simple_yaml.load('k: "true"\n'), {"k": "true"})

    def test_unbalanced_quote_falls_through(self) -> None:
        # Only a leading quote -> treated as unquoted; no comment to strip.
        self.assertEqual(simple_yaml.load('k: "abc\n'), {"k": '"abc'})


class RoundTripTests(unittest.TestCase):
    def test_dump_load_roundtrip(self) -> None:
        data = {"name": "alpha", "version": "0.0.1"}
        self.assertEqual(simple_yaml.load(simple_yaml.dump(data)), data)


class TruthyEndToEndTests(unittest.TestCase):
    def test_inline_commented_flag_loads_truthy(self) -> None:
        # The original bug: `true  # x` loaded as a string with the comment
        # glued on, so `_truthy` returned False. Assert the fix end-to-end.
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "proj"
            state_dir.mkdir()
            (state_dir / "project.yaml").write_text(
                "name: proj\n"
                "notify_leader_on_driver_done: true  # TEMP: verify, revert later\n",
                encoding="utf-8",
            )
            project = state.load_project(state_dir)
            self.assertEqual(project["notify_leader_on_driver_done"], "true")
            self.assertTrue(_truthy(project["notify_leader_on_driver_done"]))


if __name__ == "__main__":
    unittest.main()
