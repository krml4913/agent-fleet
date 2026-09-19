### fix: leader notifier injects only at a real idle boundary and verifies the submit

`leader_notifier` waited for `adapter.is_ready(pane)` before typing, but claude
keeps its `❯` composer on screen while it works, so notifications were typed into
a busy leader: surfaced mid-turn ("user sent a new message while you were
working"), or left unsent in the composer until a human pressed Enter, which then
delivered a second copy (Issue #288).

- `VendorAdapter` gains `is_busy` / `is_idle` / `composer_holds`. claude is busy
  when its spinner status line (`✢ Frosting… (48s · ↓ 4.0k tokens)`) or an
  `esc to interrupt` hint is in the bottom tail; codex matches `esc to interrupt`
  (not verified live: if the wording differs it never matches, i.e. the old
  behaviour). A vendor without a `busy` pattern is never busy, so an unknown
  pattern cannot strand the queue.
- The notifier injects only when the pane is `is_idle` (ready AND not busy) on a
  capture, and again on a confirming capture `INJECT_SETTLE_SECONDS` later taken
  right before the keystrokes. A leader that flips back to busy in between is
  left alone and the queue stays for the next boundary.
- After the submit Enter the pane is re-captured; if the text is still in the
  composer, Enter is pressed once more (`SUBMIT_ENTER_RETRIES`, mirroring the
  deliverer's `submit_retries`). The `leader_notified` event records
  `submit_confirmed` and `enter_retries`.
- Deliberately unchanged: the driver prompt deliverer keeps `is_ready` (it pastes
  into a freshly booted pane that is never mid-turn, and a busy false-positive
  there would fail the task) and the inbox wake-up nudge stays a one-shot write
  (it exists to reach a driver that may be working, and the message is durable in
  `inbox.md`).

Tests use pane fixtures built from a real claude `dump-screen` of a busy pane
(`tests/_pane_fixtures.py`).

Closes #288.
