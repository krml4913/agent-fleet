"""Stdlib-only parallel test runner for the agent-fleet unit suite.

Usage (from the repo root)::

    python tests/run_parallel.py            # all tests, one worker per CPU
    python tests/run_parallel.py -j 4       # explicit worker count
    python tests/run_parallel.py -v tests.test_start tests.test_status

``python -m unittest discover tests`` keeps working and remains the reference
serial run; this runner executes exactly the same tests (every ``TestCase``
class from ``tests/test_*.py``), just spread over a
:class:`concurrent.futures.ProcessPoolExecutor`.

How it works:

* The parent imports every test module once to enumerate its ``TestCase``
  classes, then submits one job per class, longest-first by test count so the
  big classes do not end up as the tail.
* Each worker is a long-lived interpreter (spawned once, reused for many
  classes) that runs its class with ``unittest`` in-process — module
  fixtures (``setUpModule``) and class fixtures run as usual. Test output is
  buffered (``buffer=True``) and only shown for failures.
* Results (counts + failure tracebacks) are sent back as plain data and
  aggregated. The exit status is 1 if anything failed or errored, if a worker
  crashed, or if no tests ran — so CI fails properly.

Isolation contract (what makes this safe): every test gets its own temp
``FLEET_HOME`` / state dir, restores any ``os.environ`` / cwd it changes, and
never uses fixed ports or real multiplexer session names (``FLEET_NO_MUX`` is
set by ``tests/_fleet_test_helpers.py``, which every test module imports).
Workers are separate processes, so tests never share a process concurrently —
only sequentially, exactly as in the serial run.

No third-party dependencies (design §11.3); pytest-xdist was considered and
rejected for that reason.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _init_worker() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Mirror the serial run: the helpers set the hermetic env (FLEET_NO_MUX,
    # FLEET_NO_NOTIFY, …) before any test module is imported.
    import tests._fleet_test_helpers  # noqa: F401


def _iter_cases(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


def _run_chunk(name: str) -> dict:
    """Run one ``module.Class`` in this worker and return a picklable summary."""
    start = time.perf_counter()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    runner = unittest.TextTestRunner(stream=stream, verbosity=0, buffer=True)
    result = runner.run(suite)
    return {
        "name": name,
        "run": result.testsRun,
        "failures": [(str(t), tb) for t, tb in result.failures],
        "errors": [(str(t), tb) for t, tb in result.errors],
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": [str(t) for t in result.unexpectedSuccesses],
        "seconds": time.perf_counter() - start,
    }


def _chunks(names: list[str]) -> list[tuple[str, int]]:
    """Expand module names (or pass through ``module.Class`` names) into
    ``(module.Class, n_tests)`` jobs, largest first."""
    loader = unittest.defaultTestLoader
    counts: dict[str, int] = {}
    for name in names:
        suite = loader.loadTestsFromName(name)
        for case in _iter_cases(suite):
            if isinstance(case, unittest.loader._FailedTest):  # import error
                key = name
            else:
                key = f"{type(case).__module__}.{type(case).__qualname__}"
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _default_modules() -> list[str]:
    return [f"tests.{p.stem}" for p in sorted((ROOT / "tests").glob("test_*.py"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-j", "--jobs", type=int, default=0,
        help="worker processes (default: CPU count, at most 8)",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print one line per finished chunk")
    parser.add_argument("names", nargs="*",
                        help="test modules or module.Class names (default: all)")
    args = parser.parse_args(argv)

    _init_worker()
    wall = time.perf_counter()
    jobs = _chunks(args.names or _default_modules())
    total_expected = sum(n for _, n in jobs)
    workers = args.jobs or min(os.cpu_count() or 2, 8)
    workers = max(1, min(workers, len(jobs) or 1))

    ran = skipped = xfail = 0
    failures: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    unexpected: list[str] = []
    crashed: list[str] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
        futures = {pool.submit(_run_chunk, name): name for name, _ in jobs}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                r = fut.result()
            except Exception as e:  # worker died / unpicklable result
                crashed.append(f"{name}: {type(e).__name__}: {e}")
                continue
            ran += r["run"]
            skipped += r["skipped"]
            xfail += r["expected_failures"]
            failures += r["failures"]
            errors += r["errors"]
            unexpected += r["unexpected_successes"]
            if args.verbose:
                bad = len(r["failures"]) + len(r["errors"])
                status = "FAIL" if bad else "ok"
                print(f"{r['seconds']:6.2f}s {r['run']:4d} {status:4s} {name}", flush=True)

    elapsed = time.perf_counter() - wall
    sep = "=" * 70
    for kind, items in (("FAIL", failures), ("ERROR", errors)):
        for test, tb in items:
            print(f"{sep}\n{kind}: {test}\n{'-' * 70}\n{tb}", file=sys.stderr)
    for test in unexpected:
        print(f"{sep}\nUNEXPECTED SUCCESS: {test}", file=sys.stderr)
    for line in crashed:
        print(f"{sep}\nWORKER CRASH: {line}", file=sys.stderr)

    print("-" * 70, file=sys.stderr)
    print(f"Ran {ran} tests in {elapsed:.3f}s with {workers} workers", file=sys.stderr)
    ok = not (failures or errors or unexpected or crashed) and ran > 0
    if ran != total_expected:
        ok = False
        print(f"error: expected {total_expected} tests, ran {ran}", file=sys.stderr)
    extras = []
    if failures:
        extras.append(f"failures={len(failures)}")
    if errors:
        extras.append(f"errors={len(errors)}")
    if skipped:
        extras.append(f"skipped={skipped}")
    if xfail:
        extras.append(f"expected failures={xfail}")
    if unexpected:
        extras.append(f"unexpected successes={len(unexpected)}")
    if crashed:
        extras.append(f"crashed chunks={len(crashed)}")
    tail = f" ({', '.join(extras)})" if extras else ""
    print(("OK" if ok else "FAILED") + tail, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
