"""Tests for prompt pointer paste helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import prompt_pointer  # noqa: E402
from tests._fake_mux import FakeMux  # noqa: E402


class PromptPointerTests(unittest.TestCase):
    def _prompt(self, tmp: str) -> Path:
        prompt_path = Path(tmp) / "driver-prompt.md"
        prompt_path.write_text("full prompt body\nsecond body line\n", encoding="utf-8")
        return prompt_path

    def _assert_pointer(self, text: str, prompt_path: Path) -> None:
        self.assertIn("Read the prompt file at this path", text)
        self.assertTrue(text.endswith(str(prompt_path.resolve())))
        self.assertEqual(text.count("\n"), 0)
        self.assertNotIn("full prompt body", text)
        self.assertNotIn("second body line", text)

    def test_preload_pointer_stages_sidecar_text_without_prompt_body(self) -> None:
        with TemporaryDirectory() as tmp:
            prompt_path = self._prompt(tmp)
            fake = FakeMux()

            hint = prompt_pointer.preload_pointer(fake, "fleet-task-1", prompt_path)

            self.assertEqual(hint, fake.preload_hint)
            sidecar = prompt_pointer.pointer_path(prompt_path)
            text = sidecar.read_text(encoding="utf-8")
            self.assertEqual(fake.calls_named("preload_paste"), [(("fleet-task-1", text), {})])
            self._assert_pointer(text, prompt_path)

    def test_preload_pointer_returns_none_without_backend_support(self) -> None:
        with TemporaryDirectory() as tmp:
            prompt_path = self._prompt(tmp)
            fake = FakeMux(preload_hint=None)
            self.assertIsNone(prompt_pointer.preload_pointer(fake, "b", prompt_path))

    def test_paste_pointer_pastes_pointer_text(self) -> None:
        with TemporaryDirectory() as tmp:
            prompt_path = self._prompt(tmp)
            fake = FakeMux()

            path = prompt_pointer.paste_pointer(
                fake, session="fleet-main", window="1·driver", prompt_path=prompt_path
            )

            self.assertEqual(path, prompt_pointer.pointer_path(prompt_path))
            (session, window, text), _kw = fake.calls_named("paste")[0]
            self.assertEqual((session, window), ("fleet-main", "1·driver"))
            self.assertEqual(text, path.read_text(encoding="utf-8"))
            self._assert_pointer(text, prompt_path)


if __name__ == "__main__":
    unittest.main()
