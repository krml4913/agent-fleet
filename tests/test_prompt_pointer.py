"""Tests for prompt pointer paste helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import prompt_pointer  # noqa: E402


class PromptPointerTests(unittest.TestCase):
    def test_load_pointer_buffer_uses_sidecar_without_prompt_body(self) -> None:
        with TemporaryDirectory() as tmp:
            prompt_path = Path(tmp) / "driver-prompt.md"
            prompt_path.write_text("full prompt body\nmixed: 日本語\n", encoding="utf-8")
            tmux = MagicMock()

            pointer_path = prompt_pointer.load_pointer_buffer(
                tmux,
                "fleet-task-1",
                prompt_path,
            )

            self.assertEqual(pointer_path, prompt_pointer.pointer_path(prompt_path))
            tmux.load_buffer.assert_called_once_with("fleet-task-1", str(pointer_path))
            text = pointer_path.read_text(encoding="utf-8")
            self.assertIn("Read the prompt file at this path", text)
            self.assertTrue(text.endswith(str(prompt_path.resolve())))
            self.assertEqual(text.count("\n"), 0)
            self.assertNotIn("full prompt body", text)
            self.assertNotIn("日本語", text)


if __name__ == "__main__":
    unittest.main()
