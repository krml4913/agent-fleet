"""Tests for the ``fleet leader`` startup banner (``fleet.banner``)."""
from __future__ import annotations

import io
import os
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import banner  # noqa: E402

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ART = banner.ART_PATH.read_text(encoding="utf-8")
_ART_LINES = _ART.splitlines()
_SHIPS = _ART_LINES[:13]
_LOGO = _ART_LINES[14:]
_ART_WIDTH = max(len(ln) for ln in _ART_LINES)
# The logo shown without the ships: dedented, then re-indented by banner.INDENT.
_LOGO_INDENT = min(len(ln) - len(ln.lstrip(" ")) for ln in _LOGO)
_LOGO_ONLY = [banner.INDENT + ln[_LOGO_INDENT:] for ln in _LOGO]
_LOGO_ONLY_WIDTH = max(len(ln) for ln in _LOGO_ONLY)

_WHITE_BOLD = "\x1b[1;37m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"
_CYAN_BOLD = "\x1b[1;36m"
_RESET = "\x1b[0m"


def _paint(style: str, text: str) -> str:
    return f"{style}{text}{_RESET}"


class _TtyStream(io.StringIO):
    """A ``StringIO`` that claims to be a terminal (and has no encoding)."""

    def isatty(self) -> bool:
        return True


def _stream_with_encoding(encoding: str) -> tuple[io.TextIOWrapper, io.BytesIO]:
    """A real strict-encoding text stream — writing an unencodable char raises."""
    raw = io.BytesIO()
    # newline="\n": no "\n" → "\r\n" translation on Windows.
    return io.TextIOWrapper(raw, encoding=encoding, newline="\n", write_through=True), raw


class ArtAssetTests(unittest.TestCase):
    def test_asset_is_ships_blank_line_logo(self) -> None:
        self.assertEqual(len(_ART_LINES), 20)
        self.assertEqual(_ART_LINES[13], "")
        self.assertIn("|>>>", _SHIPS[0])
        self.assertIn("███████╗██╗", _LOGO[0])

    def test_plain_full_render_is_the_asset_verbatim(self) -> None:
        out = banner.render_art(columns=_ART_WIDTH, color=False, encoding="utf-8")
        self.assertEqual(out, _ART)
        self.assertNotIn("\x1b", out)


class ColorTests(unittest.TestCase):
    def _color_lines(self) -> list[str]:
        return banner.render_art(columns=200, color=True, encoding="utf-8").split("\n")

    def test_color_render_strips_back_to_the_plain_art(self) -> None:
        colored = banner.render_art(columns=200, color=True, encoding="utf-8")
        self.assertIn("\x1b[", colored)
        self.assertEqual(_ANSI.sub("", colored), _ART)

    def test_sails_masts_and_pennants_are_white_bold(self) -> None:
        lines = self._color_lines()
        self.assertIn(_paint(_WHITE_BOLD, "|>>>"), lines[0])
        self.assertIn(_paint(_WHITE_BOLD, ")_)"), lines[3])
        self.assertIn(_paint(_WHITE_BOLD, "|>"), lines[4])
        # The sail row above a hull stays rigging, even next to the hull.
        self.assertIn(_paint(_WHITE_BOLD, ")___)"), lines[8])

    def test_hulls_are_yellow(self) -> None:
        lines = self._color_lines()
        self.assertIn(_paint(_YELLOW, "___|______|__________|______|___"), lines[8])
        self.assertIn(_paint(_YELLOW, "_|_____|___"), lines[9])
        self.assertIn(_paint(_YELLOW, "\\____________________________/"), lines[10])

    def test_sea_is_blue_and_hull_bottoms_flanked_by_sea_stay_yellow(self) -> None:
        lines = self._color_lines()
        self.assertIn(_paint(_BLUE, "~~~~"), lines[11])
        self.assertIn(_paint(_YELLOW, "\\_______/"), lines[11])
        # Every ``~`` is blue and nothing else is.
        blue = "".join(_ANSI.sub("", s) for s in re.findall(re.escape(_BLUE) + r"([^\x1b]*)", "\n".join(lines[:13])))
        self.assertEqual(set(blue), {"~"})
        self.assertEqual(blue.count("~"), "\n".join(_SHIPS).count("~"))

    def test_logo_is_cyan_bold(self) -> None:
        lines = self._color_lines()
        for plain, colored in zip(_LOGO, lines[14:20]):
            body = plain.lstrip(" ")
            self.assertEqual(colored, plain[: len(plain) - len(body)] + _paint(_CYAN_BOLD, body))


class WidthFallbackTests(unittest.TestCase):
    def _render(self, columns: int) -> str:
        return banner.render_art(columns=columns, color=False, encoding="utf-8")

    def test_wide_terminal_shows_ships_and_logo(self) -> None:
        for columns in (_ART_WIDTH, _ART_WIDTH + 40):
            out = self._render(columns)
            self.assertIn("|>>>", out)
            self.assertIn("███████╗██╗", out)

    def test_narrower_than_the_art_but_wide_enough_for_the_logo_shows_logo_only(self) -> None:
        for columns in (_ART_WIDTH - 1, _LOGO_ONLY_WIDTH):
            out = self._render(columns)
            self.assertEqual(out, "\n".join(_LOGO_ONLY) + "\n")
            self.assertNotIn("|>>>", out)
            self.assertNotIn("~", out)

    def test_logo_only_fits_within_the_terminal(self) -> None:
        out = self._render(_LOGO_ONLY_WIDTH)
        self.assertLessEqual(max(len(ln) for ln in out.splitlines()), _LOGO_ONLY_WIDTH)

    def test_narrower_than_the_logo_shows_no_art(self) -> None:
        self.assertEqual(self._render(_LOGO_ONLY_WIDTH - 1), "")
        self.assertEqual(self._render(10), "")

    def test_logo_only_is_colored_like_the_full_logo(self) -> None:
        out = banner.render_art(columns=_LOGO_ONLY_WIDTH, color=True, encoding="utf-8")
        self.assertEqual(_ANSI.sub("", out), "\n".join(_LOGO_ONLY) + "\n")
        self.assertIn(_CYAN_BOLD, out)
        self.assertNotIn(_WHITE_BOLD, out)


class EncodingFallbackTests(unittest.TestCase):
    def test_unencodable_console_skips_the_art_entirely(self) -> None:
        for encoding in ("ascii", "cp1252", "latin-1"):
            with self.subTest(encoding=encoding):
                out = banner.render_art(columns=200, color=False, encoding=encoding)
                self.assertEqual(out, "")

    def test_utf8_and_unknown_encodings_render(self) -> None:
        for encoding in ("utf-8", "UTF-8", None):
            with self.subTest(encoding=encoding):
                out = banner.render_art(columns=200, color=False, encoding=encoding)
                self.assertEqual(out, _ART)

    def test_bogus_encoding_name_is_treated_as_unencodable(self) -> None:
        self.assertEqual(banner.render_art(columns=200, color=False, encoding="no-such-codec"), "")

    def test_info_line_avoids_the_middle_dot_when_it_cannot_be_encoded(self) -> None:
        self.assertEqual(
            banner.format_info("main", "claude:opus", [], encoding="ascii"),
            "  leader: main | agent: claude:opus | scope: all",
        )

    def test_print_banner_on_an_ascii_stream_writes_only_the_info_line(self) -> None:
        stream, raw = _stream_with_encoding("ascii")
        with unittest.mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((200, 50))):
            banner.print_banner(label="main", agent="claude:opus", scope=[], stream=stream)
        self.assertEqual(
            raw.getvalue().decode("ascii"),
            "  leader: main | agent: claude:opus | scope: all\n",
        )


class InfoLineTests(unittest.TestCase):
    def test_unscoped_is_all(self) -> None:
        self.assertEqual(
            banner.format_info("main", "claude:opus", []),
            "  leader: main · agent: claude:opus · scope: all",
        )

    def test_scoped_is_a_comma_list(self) -> None:
        self.assertEqual(
            banner.format_info("main", "claude:opus", ["image-gallery", "fleet"]),
            "  leader: main · agent: claude:opus · scope: image-gallery, fleet",
        )


class PrintBannerTests(unittest.TestCase):
    """``print_banner`` decides color from the stream + ``NO_COLOR`` and width from the terminal."""

    def _print(self, stream, *, columns: int = 100, env: dict[str, str] | None = None, scope=None) -> None:
        env = env or {}
        with (
            unittest.mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((columns, 50))),
            unittest.mock.patch.dict(os.environ, env),
        ):
            if "NO_COLOR" not in env:
                os.environ.pop("NO_COLOR", None)
            banner.print_banner(label="main", agent="claude:opus", scope=scope or [], stream=stream)

    def test_non_tty_is_plain(self) -> None:
        stream = io.StringIO()
        self._print(stream)
        out = stream.getvalue()
        self.assertNotIn("\x1b", out)
        self.assertEqual(out, _ART + "  leader: main · agent: claude:opus · scope: all\n")

    def test_tty_is_colored(self) -> None:
        stream = _TtyStream()
        self._print(stream)
        out = stream.getvalue()
        for code in (_WHITE_BOLD, _YELLOW, _BLUE, _CYAN_BOLD):
            self.assertIn(code, out)
        self.assertEqual(
            _ANSI.sub("", out), _ART + "  leader: main · agent: claude:opus · scope: all\n"
        )

    def test_no_color_disables_color_on_a_tty(self) -> None:
        for value in ("1", ""):  # presence is what counts, as in ``fleet sessions``
            with self.subTest(NO_COLOR=value):
                stream = _TtyStream()
                self._print(stream, env={"NO_COLOR": value})
                self.assertNotIn("\x1b", stream.getvalue())
                self.assertIn("███████╗██╗", stream.getvalue())

    def test_width_fallbacks_end_at_the_info_line(self) -> None:
        info = "  leader: main · agent: claude:opus · scope: fleet\n"
        cases = {
            _ART_WIDTH: _ART + info,
            _ART_WIDTH - 1: "\n".join(_LOGO_ONLY) + "\n" + info,
            _LOGO_ONLY_WIDTH - 1: info,
        }
        for columns, expected in cases.items():
            with self.subTest(columns=columns):
                stream = io.StringIO()
                self._print(stream, columns=columns, scope=["fleet"])
                self.assertEqual(stream.getvalue(), expected)

    def test_render_error_skips_the_art_but_keeps_the_info_line(self) -> None:
        stream = io.StringIO()
        with unittest.mock.patch("fleet.banner.render_art", side_effect=RuntimeError("boom")):
            self._print(stream)
        self.assertEqual(stream.getvalue(), "  leader: main · agent: claude:opus · scope: all\n")

    def test_missing_asset_never_raises(self) -> None:
        stream = io.StringIO()
        with unittest.mock.patch("fleet.banner.ART_PATH", ROOT / "no-such-banner.txt"):
            self._print(stream)
        self.assertEqual(stream.getvalue(), "  leader: main · agent: claude:opus · scope: all\n")

    def test_a_broken_stream_never_raises(self) -> None:
        class Broken:
            encoding = "utf-8"

            def isatty(self) -> bool:
                raise OSError("closed")

            def write(self, _text: str) -> int:
                raise OSError("closed")

        self._print(Broken())  # must not raise


if __name__ == "__main__":
    unittest.main()
