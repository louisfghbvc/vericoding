"""What verdict comes back when something goes wrong rather than fails.

A timeout is not a counterexample. A crashed solver is not a counterexample.
A missing Dafny is not a counterexample. Each one means nothing was checked,
and reporting any of them as PROOF_FAILED sends someone to debug a
specification that was never examined -- the same conflation the four-state
verdict exists to prevent, arriving through the error handlers instead of
through the exit code.

Coverage found these paths unexercised at 52% on verify_loop.py and 56% on
compiler.py, after an earlier claim in this session that what remained was
"CLI formatting, not decision logic". It was not.
"""

import subprocess

import pytest

from scripts.compiler import DafnyCompiler
from scripts.verify_loop import DafnyVerifier, STATUS_TOOLCHAIN


def test_a_missing_dafny_is_a_toolchain_error_not_a_failure():
    res = DafnyVerifier(dafny_path="/nonexistent/dafny").verify("anything.dfy")
    assert res["status"] == STATUS_TOOLCHAIN
    assert res["verified"] is False
    assert res["success"] is False
    assert "not found" in res["error"].lower()


def test_a_timeout_is_a_toolchain_error_not_a_failure(monkeypatch, tmp_path):
    """A solver that ran out of time proved nothing. It also disproved
    nothing, and a caller retrying the spec instead of raising the timeout
    will retry forever."""
    dafny = tmp_path / "dafny"
    dafny.write_text("#!/bin/sh\n")
    verifier = DafnyVerifier(dafny_path=str(dafny))

    from scripts import verify_loop as mod

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = verifier.verify(str(tmp_path / "x.dfy"), timeout_seconds=30)

    assert res["status"] == STATUS_TOOLCHAIN
    assert res["verified"] is False
    assert "timed out" in res["error"]
    assert "30s" in res["error"], "the budget that was exceeded has to be in the message"


def test_an_unexpected_crash_is_a_toolchain_error_not_a_failure(monkeypatch, tmp_path):
    dafny = tmp_path / "dafny"
    dafny.write_text("#!/bin/sh\n")
    verifier = DafnyVerifier(dafny_path=str(dafny))

    from scripts import verify_loop as mod

    def fake_run(cmd, **kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = verifier.verify(str(tmp_path / "x.dfy"))

    assert res["status"] == STATUS_TOOLCHAIN
    assert res["verified"] is False
    assert "Exec format error" in res["error"]


def test_a_missing_dafny_is_reported_by_the_compiler_too():
    res = DafnyCompiler(dafny_path="/nonexistent/dafny").compile("x.dfy", target="py")
    assert res["success"] is False
    assert "not found" in res["error"].lower()


@pytest.mark.parametrize("target,needle", [
    ("go", "goimports"),
    ("js", "bignumber.js"),
    ("cs", ".NET SDK"),
])
def test_a_failed_build_names_the_toolchain_it_needed(monkeypatch, tmp_path, target, needle):
    """Dafny verifies all four targets but only `py` completes without a
    further toolchain. The failure arrived as a raw goimports or dotnet stack
    trace, which does not tell the reader that the missing piece was never
    mentioned by this project."""
    dafny = tmp_path / "dafny"
    dafny.write_text("#!/bin/sh\n")
    spec = tmp_path / "x.dfy"
    spec.write_text("method M() {}")

    from scripts import compiler as mod

    class _Proc:
        returncode = 3
        stdout = "some raw third-party stack trace"
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: _Proc())
    res = DafnyCompiler(dafny_path=str(dafny)).compile(
        str(spec), target=target, output_dir=str(tmp_path / "o"))

    assert res["success"] is False
    assert needle in res["error"], res["error"]
    # And it must say the proof did not depend on it, or the reader concludes
    # the verification is what broke.
    assert "verification itself does not" in res["error"]


def test_a_successful_py_build_names_no_extra_requirement(monkeypatch, tmp_path):
    dafny = tmp_path / "dafny"
    dafny.write_text("#!/bin/sh\n")
    spec = tmp_path / "x.dfy"
    spec.write_text("method M() {}")

    from scripts import compiler as mod

    class _Proc:
        returncode = 3
        stdout = ""
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: _Proc())
    res = DafnyCompiler(dafny_path=str(dafny)).compile(
        str(spec), target="py", output_dir=str(tmp_path / "o"))
    assert res["success"] is False
    assert "additionally requires" not in res["error"], \
        "py needs nothing beyond Dafny; claiming otherwise misdirects the reader"
