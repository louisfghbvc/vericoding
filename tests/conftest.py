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
