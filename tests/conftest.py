"""Shared test fixtures.

The one thing worth explaining here is why `dafny --version` succeeding is not
enough to decide a test can run.

Dafny ships a bundled Z3, and on several hosts that binary does not execute --
it is built against a newer glibc than the distribution provides. Dafny itself
starts fine, so `shutil.which("dafny")` and `is_available()` both say yes; the
failure only appears when a verification is actually attempted, and it appears
as exit code 4 with no verification summary.

Tests that guarded on `is_available()` alone therefore ran on such a host and
failed with `assert False is True` -- reporting a toolchain gap as a defect in
the code, which is the same conflation the four-state verdict in verify_loop
exists to prevent. The probe below asks the question that actually matters:
can a verification complete at all?
"""

import tempfile
import os

import pytest

from scripts.verify_loop import DafnyVerifier, STATUS_TOOLCHAIN

# A specification small enough that a failure to verify it can only be the
# toolchain, never the content.
_PROBE_SOURCE = """
method Probe(x: int) returns (y: int)
  ensures y == x
{ y := x; }
"""


def _probe_toolchain():
    """Return None if a verification can complete, else why it cannot."""
    verifier = DafnyVerifier()
    if not verifier.is_available():
        return "Dafny CLI not installed"

    handle, path = tempfile.mkstemp(suffix=".dfy", prefix="vericoding-probe-")
    try:
        with os.fdopen(handle, "w") as f:
            f.write(_PROBE_SOURCE)
        result = verifier.verify(path, timeout_seconds=60)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if result.get("status") == STATUS_TOOLCHAIN:
        return "Dafny present but no verification could run: {}".format(
            result.get("error") or result.get("detail")
        )
    return None


@pytest.fixture(scope="session")
def toolchain():
    """Skip the requesting test unless a verification can actually complete.

    Session-scoped, so the probe runs once per test session rather than once
    per test.
    """
    reason = _probe_toolchain()
    if reason:
        pytest.skip(reason)
    return True


@pytest.fixture(autouse=True, scope="session")
def _examples_are_read_only():
    """Restore examples/ after the session, whatever the tests did to it.

    Defence in depth behind "tests should use copies". A suite that mutates
    committed fixtures is one interrupted run away from a false failure: an
    aborted run left bank.receipt.json inconsistent with the bank.smt2 beside
    it, and the next run reported four failures that were not defects. It
    also makes `git status` permanently dirty, so a real change hides in the
    churn.

    Snapshot-and-restore rather than chmod, so a test that legitimately needs
    to write still can -- it just cannot leave the tree changed.
    """
    import pathlib
    import shutil
    import tempfile

    examples = pathlib.Path(__file__).resolve().parent.parent / "examples"
    if not examples.is_dir():
        yield
        return

    backup = tempfile.mkdtemp(prefix="vericoding-examples-")
    shutil.copytree(examples, pathlib.Path(backup) / "examples")
    try:
        yield
    finally:
        shutil.rmtree(examples, ignore_errors=True)
        shutil.copytree(pathlib.Path(backup) / "examples", examples)
        shutil.rmtree(backup, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_leaked_stubs():
    """Fail the test that leaves a stub behind, instead of the one that trips
    over it.

    `test_diagnostics_probe_every_documented_target` used to patch two module
    attributes by hand and restore one, so `check_env.probe_verification`
    stayed stubbed to `{"ok": True}` for the rest of the session. Nothing went
    red. The suite stayed green for many runs while every later test that
    touched the real probe was quietly reading a stub that always says yes --
    including tests whose whole subject is whether verification is possible on
    this host.

    That is the gate-vacuity pattern one level down: a check that cannot fail
    because the thing it inspects was replaced by something that always
    passes. It is worth a guard precisely because the symptom appears far from
    the cause -- when it finally surfaced it looked like an order-dependent
    bug in an unrelated new test.

    A callable on a `scripts.*` module that was defined in a test module is
    always a leak; no production code defines functions there.
    """
    import sys

    yield

    leaked = []
    for mod_name, module in list(sys.modules.items()):
        if not mod_name.startswith("scripts.") or module is None:
            continue
        for attr_name, value in list(vars(module).items()):
            origin = getattr(value, "__module__", None)
            if not callable(value) or not origin:
                continue
            if origin.startswith("test_") or origin.startswith("tests."):
                leaked.append("{}.{} <- {}".format(mod_name, attr_name, origin))

    assert not leaked, (
        "test-double left installed after the test finished; every later test "
        "sees it:\n  " + "\n  ".join(leaked)
    )
