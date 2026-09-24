"""Tests for ``fleet url-handler`` (#317) — parse/validate + dispatch fleet:// URIs.

Hermetic: no real subprocess is spawned (``open_attach_terminal`` is patched
or its ``subprocess.Popen`` call is), and every project/task lookup runs
against a throwaway ``FLEET_HOME``.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

import tests._fleet_test_helpers as helpers  # noqa: E402
from fleet import state  # noqa: E402
from fleet.commands import url_handler  # noqa: E402


class ParseUriTests(unittest.TestCase):
    def test_valid_attach_uri(self) -> None:
        target = url_handler.parse_uri("fleet://attach?project=myproj&task=42")
        self.assertEqual(target, url_handler.AttachTarget(project="myproj", task_id="42"))

    def test_wrong_scheme_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("http://attach?project=p&task=1"))

    def test_unknown_action_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://approve?project=p&task=1"))

    def test_missing_project_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?task=1"))

    def test_missing_task_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p"))

    def test_duplicated_field_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p&project=q&task=1"))

    def test_empty_uri_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri(""))

    def test_garbage_uri_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("not a uri at all"))

    def test_bare_path_form_accepted(self) -> None:
        # Some URL parsers put "attach" in .path rather than .netloc for a
        # triple-slash form; accept it too.
        target = url_handler.parse_uri("fleet:///attach?project=p&task=1")
        self.assertEqual(target, url_handler.AttachTarget(project="p", task_id="1"))

    # -- injection attempts (issue #317 acceptance criteria) ----------------------

    def test_ampersand_in_value_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=a%26b&task=1"))

    def test_double_quote_in_value_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri('fleet://attach?project=a%22b&task=1'))

    def test_percent_in_value_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=a%25b&task=1"))

    def test_space_in_value_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=a+b&task=1"))
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=a%20b&task=1"))

    def test_dotdot_in_value_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=..&task=1"))
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p&task=../../etc"))

    def test_path_separators_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p&task=a/b"))
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p&task=a%5Cb"))

    def test_semicolon_and_shell_metachars_rejected(self) -> None:
        for bad in ("a;b", "a|b", "a`b", "a$(b)", "a<b", "a>b"):
            with self.subTest(bad=bad):
                self.assertIsNone(url_handler.parse_uri(f"fleet://attach?project={bad}&task=1"))

    def test_plain_alnum_hyphen_underscore_accepted(self) -> None:
        target = url_handler.parse_uri("fleet://attach?project=my-proj_1x&task=task-42")
        self.assertEqual(
            target, url_handler.AttachTarget(project="my-proj_1x", task_id="task-42")
        )

    def test_dot_rejected(self) -> None:
        # No dot is ever valid in a project/task slug — this alone keeps ".."
        # out without a separate traversal check.
        self.assertIsNone(url_handler.parse_uri("fleet://attach?project=p&task=1.0"))


class RunDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project_repo = Path(self._tmp.name) / "repo"
        self.project_repo.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = helpers.make_project(self.fleet_home, "demo", self.project_repo)
        state.save_task(self.state_dir, "1", {"id": "1"})

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _run(self, uri: str):
        import argparse

        args = argparse.Namespace(uri=uri)
        with mock.patch.object(url_handler, "open_attach_terminal", return_value=0) as opened:
            rc = url_handler.run(args)
        return rc, opened

    def test_valid_attach_opens_terminal(self) -> None:
        rc, opened = self._run("fleet://attach?project=demo&task=1")
        self.assertEqual(rc, 0)
        opened.assert_called_once_with("demo", "1")

    def test_unknown_project_rejected_without_opening(self) -> None:
        rc, opened = self._run("fleet://attach?project=no-such&task=1")
        self.assertNotEqual(rc, 0)
        opened.assert_not_called()

    def test_unknown_task_rejected_without_opening(self) -> None:
        rc, opened = self._run("fleet://attach?project=demo&task=999")
        self.assertNotEqual(rc, 0)
        opened.assert_not_called()

    def test_invalid_uri_rejected_without_opening(self) -> None:
        rc, opened = self._run("fleet://bogus?x=1")
        self.assertNotEqual(rc, 0)
        opened.assert_not_called()

    def test_rejections_are_logged(self) -> None:
        self._run("fleet://attach?project=no-such&task=1")
        log_path = state.global_dir() / url_handler.LOG_NAME
        self.assertTrue(log_path.is_file())
        self.assertIn("no-such", log_path.read_text(encoding="utf-8"))

    def test_valid_attach_is_logged(self) -> None:
        self._run("fleet://attach?project=demo&task=1")
        log_path = state.global_dir() / url_handler.LOG_NAME
        self.assertIn("project=demo task=1", log_path.read_text(encoding="utf-8"))


class AttachArgvTests(unittest.TestCase):
    """Argv construction never goes through a shell string."""

    def setUp(self) -> None:
        # open_attach_terminal()'s failure path calls _log(), which writes
        # under FLEET_HOME/global/ — isolate it so a failure test never
        # touches the real fleet-state tree.
        self._tmp = TemporaryDirectory()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_argv_uses_cmd_slash_c_and_fleet_cmd(self) -> None:
        argv = url_handler.attach_argv("myproj", "42")
        self.assertIn("/c", argv)
        self.assertTrue(any(a.endswith("fleet.cmd") for a in argv))
        self.assertIn("attach", argv)
        self.assertIn("42", argv)
        self.assertIn("--project", argv)
        self.assertIn("myproj", argv)

    def test_argv_is_a_flat_list_no_shell_string(self) -> None:
        argv = url_handler.attach_argv("a", "b")
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(a, str) for a in argv))

    def test_open_attach_terminal_never_uses_shell_true(self) -> None:
        with mock.patch("fleet.commands.url_handler.shutil.which", return_value=None), \
             mock.patch("fleet.commands.url_handler.subprocess.Popen") as popen:
            url_handler.open_attach_terminal("p", "1")
        popen.assert_called_once()
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_prefers_windows_terminal_when_present(self) -> None:
        with mock.patch(
            "fleet.commands.url_handler.shutil.which",
            side_effect=lambda name: r"C:\wt.exe" if "wt" in name else None,
        ), mock.patch("fleet.commands.url_handler.subprocess.Popen") as popen:
            url_handler.open_attach_terminal("p", "1")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], r"C:\wt.exe")

    def test_popen_failure_is_reported_not_raised(self) -> None:
        with mock.patch("fleet.commands.url_handler.shutil.which", return_value=None), \
             mock.patch(
                 "fleet.commands.url_handler.subprocess.Popen", side_effect=OSError("nope")
             ):
            rc = url_handler.open_attach_terminal("p", "1")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
