### test: isolate the global tier from the developer's real `fleet-state/` (#319)

`test_load_formation_does_not_fall_back_to_template` failed on any clone with a seeded
`fleet-state/global/formations/` (it found the real `pair_review.yaml` instead of raising
`FileNotFoundError`); CI never saw it because nothing is seeded there. The test used a temp
*project* state dir, but the global tier still resolved from the real clone, because most
tests never set `$FLEET_HOME` and fell through to `<clone>/fleet-state`.

`tests/_fleet_test_helpers.py` (imported by every test module) now points the ambient
`$FLEET_HOME` at a throwaway temp dir before fleet is imported, overriding any inherited
value and removing the dir at exit. Beyond the failing test, this stops ~300 tests from
rewriting the real `global/dashboard.html` (and `test_merge` from touching
`global/sessions/<label>/leader-pending.jsonl`) on every run. `FormationTemplateTests`
additionally gets its own empty global tier per test, since it asserts on the
project → global lookup cascade. Tests that need a populated tier still set `$FLEET_HOME`
themselves, as before.

Verified on a copy of a seeded clone: `run_parallel.py` and `unittest discover` are green,
the seeded `fleet-state/` is byte-identical afterwards, and the suite still passes with
`fleet-state/` made unreadable (chmod 000), i.e. no test reads or writes it.
