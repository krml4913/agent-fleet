### fix: the leader notifier no longer types on top of an unsent human draft (#329)

The leader once received `1. teams.[fleet] 1 driver notification(s) … PR=…/pull/3` for a
task whose PR was #328. The URL was right all the way to the pane (`build_record` and
`render_block` produce `pull/328` for that task's outbox; a regression test pins it): the
user had a half-written reply in the leader's composer, `is_idle` only checked "prompt
visible and not busy", and the notifier typed its block after the draft. The two texts
were merged and the tail of the URL was lost. A wrong PR number is dangerous, because the
leader may review or merge the wrong PR.

- `VendorAdapter.has_draft(pane)` reads the composer (the text after the `❯` glyph up to
  the rule closing it) and `is_idle` now requires it to be empty. The hints an empty
  composer shows (`Try "…"`, `Press up to edit queued messages`) are not a draft. It is claude-only: a vendor without a `composer_end`
  pattern (codex) never reports one, so an unknown layout cannot strand the queue.
- The notifier just keeps waiting; the records stay queued. A draft that outlives the
  poller re-arms a successor like a busy leader does, so nothing is dropped and the
  notification is delivered once the composer is empty. `leader-notifier.log` says
  `leader has an unsent draft in its composer; waiting`.
