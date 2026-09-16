"""What happens when the solver, the emitter, or the source is not there.

Each of these paths ends in a receipt that claims LESS than a normal run
would. That is the direction they must fail in, and none of them was
exercised -- so the code that keeps a broken run from producing a confident
receipt was itself unverified.
"""

import os
import subprocess

import pytest

from scripts import receipt_generator
from scripts.receipt_generator import ReceiptGenerator, replay_proof


# --- replay_proof ------------------------------------------------------

def test_a_file_with_no_verdict_is_not_a_proof(tmp_path, toolchain):
    """The fabricated-stub defense, at its narrowest.

    A file the solver reads without ever answering sat or unsat establishes
    nothing. `checked: False` is the only honest result -- and it must not be
    `ok: True`, which is what "no failures were seen" would decay into.
    """
    smt2 = tmp_path / "silent.smt2"
    smt2.write_text("(set-logic ALL)\n; no query at all\n")
    result = replay_proof(str(smt2))
    assert result["checked"] is False
    assert "no sat/unsat" in result["reason"]
    assert "ok" not in result, "an unchecked artifact was given a pass/fail verdict"


def test_a_solver_that_cannot_run_is_reported_not_swallowed(tmp_path):
    """Pointing at something that is not an executable raises inside the
    subprocess call. It has to surface as 'nothing ran', not as a proof that
    happens not to have failed."""
    smt2 = tmp_path / "p.smt2"
    smt2.write_text("(check-sat)\n")
    result = replay_proof(str(smt2), solver_path=str(tmp_path))  # a directory
    assert result["checked"] is False
    assert "ok" not in result


def test_a_missing_artifact_cannot_be_replayed(tmp_path):
    result = replay_proof(str(tmp_path / "absent.smt2"))
    assert result["checked"] is False
    assert "missing" in result["reason"]


def test_a_real_unsat_artifact_replays(tmp_path, toolchain):
    """The positive control. Without it the tests above would also pass
    against a replay_proof that always says 'checked: False'."""
    smt2 = tmp_path / "good.smt2"
    smt2.write_text("(assert (and (> 1 0) (< 1 0)))\n(check-sat)\n")
    result = replay_proof(str(smt2))
    assert result["checked"] is True and result["ok"] is True


# --- generation edges ---------------------------------------------------

def test_generating_a_receipt_for_a_missing_file_refuses(tmp_path):
    """A receipt for a file that is not there would attest to nothing."""
    with pytest.raises(FileNotFoundError):
        ReceiptGenerator().generate(str(tmp_path / "absent.dfy"))


def test_an_emitter_that_crashes_yields_no_artifact_not_a_bad_one(tmp_path, monkeypatch):
    """The failure that produced the stub originally.

    When emission blows up the receipt must record having no proof. Writing
    something -- anything -- and hashing it is how the earlier version came to
    attest to a five-line file containing no assertions.
    """
    def explode(*args, **kwargs):
        raise OSError("dafny vanished mid-run")

    monkeypatch.setattr(subprocess, "run", explode)
    gen = ReceiptGenerator()
    monkeypatch.setattr(gen.verifier, "is_available", lambda: True)
    artifact = gen._emit_proof_artifact(str(tmp_path / "x.dfy"))
    assert artifact["replayable"] is False
    assert artifact["path"] is None
    assert "emission failed" in artifact["reason"]


def test_an_unreadable_proof_log_yields_no_artifact(tmp_path, monkeypatch):
    """The log exists but cannot be read. Same rule: record the absence."""
    target = tmp_path / "x.dfy"
    target.write_text("method M() {}\n")
    log = tmp_path / "x.smt2"

    def fake_run(*args, **kwargs):
        log.write_text("(assert true)\n" * 200)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    real_open = open

    def refuse(path, *args, **kwargs):
        if str(path) == str(log):
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", refuse)
    gen = ReceiptGenerator()
    monkeypatch.setattr(gen.verifier, "is_available", lambda: True)
    artifact = gen._emit_proof_artifact(str(target))
    assert artifact["replayable"] is False
    assert "unreadable" in artifact["reason"]
