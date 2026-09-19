### test: cut unit-suite wall time ~4x serial, ~10x with the new parallel runner

`run_fleet` / `run_fleet_agent` now call `fleet.cli.main` / `main_agent`
in-process (env, cwd, stdin, and the mux backend isolated per call;
`FLEET_TEST_SUBPROCESS=1` restores real subprocesses). A few true
end-to-end subprocess smoke tests remain. Also: fsync skipped in tests through
a new `fleet.locking.fsync` hook (production still uses `os.fsync`), shared
git-repo and `preflight.check_all()` fixtures, no more waited-out sleeps or
socket timeouts, and every test module now imports the hermetic-env helpers,
so running a module alone no longer sends real desktop toasts. New stdlib
`tests/run_parallel.py` (a ProcessPoolExecutor, one job per TestCase class)
is what CI now runs; `python -m unittest discover tests` still works and runs
in one CI job as the serial reference. Windows local: 88 s → 22 s serial,
~9 s parallel.
