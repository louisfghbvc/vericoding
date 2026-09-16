"""The checks that stop a fabricated proof artifact from being recorded.

This repository shipped with a five-line stub -- `(set-logic ALL)` and
`(check-sat)`, no assertions -- written by the generator itself when Dafny
produced no log, hashed into the receipt, and recorded as replayable. It
re-checked as `sat`, because an empty query is trivially satisfiable.

The validation added to stop that recurring was itself untested: coverage
measured 59% on receipt_generator.py with the whole rejection path
unexercised. A guard nobody has seen fail is a guard nobody knows works.

Dafny is stubbed here on purpose -- what is under test is what the generator
does with the bytes it gets back, not whether Dafny runs.
"""

import os

import pytest

from scripts.receipt_generator import ReceiptGenerator


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def _generator_writing(monkeypatch, content, tmp_path):
    """A ReceiptGenerator whose Dafny writes `content` as the proof log."""
    gen = ReceiptGenerator(dafny_path=str(tmp_path / "fake-dafny"))
    (tmp_path / "fake-dafny").write_text("#!/bin/sh\n")
    monkeypatch.setattr(gen.verifier, "is_available", lambda: True)

    from scripts import receipt_generator as mod

    def fake_run(cmd, **kwargs):
        out = next((a.split(":", 1)[1] for a in cmd if a.startswith("/proverLog:")), None)
        if out is not None and content is not None:
            with open(out, "w") as handle:
                handle.write(content)
        return _Proc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return gen


REAL_ENOUGH = (
    "(set-option :print-success false)\n"
    + "(declare-const x Int)\n"
    + "(assert (> x 0))\n" * 200
    + "(check-sat)\n"
)


def test_accepts_a_log_that_looks_like_a_proof(monkeypatch, tmp_path):
    gen = _generator_writing(monkeypatch, REAL_ENOUGH, tmp_path)
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")

    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is True
    assert result["assertion_count"] == 200
    assert os.path.exists(result["path"])


def test_rejects_and_deletes_the_stub_that_shipped(monkeypatch, tmp_path):
    """The exact five lines the generator used to fabricate."""
    gen = _generator_writing(
        monkeypatch,
        "; SMT-LIB2 Verification Proof Artifact\n(set-logic ALL)\n(check-sat)\n",
        tmp_path,
    )
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")

    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is False
    assert result["path"] is None
    assert "no (assert" in result["reason"]
    # Deleted, not merely unrecorded: a rejected file left on disk is the next
    # reader's evidence.
    assert not os.path.exists(str(tmp_path / "spec.smt2"))


def test_rejects_a_file_too_small_to_be_a_proof(monkeypatch, tmp_path):
    gen = _generator_writing(
        monkeypatch, "(set-option :x)\n(assert true)\n(check-sat)\n", tmp_path)
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")
    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is False
    assert "bytes" in result["reason"]


def test_rejects_a_file_that_is_not_smt_lib2(monkeypatch, tmp_path):
    gen = _generator_writing(
        monkeypatch, "not smt at all\n" + "(assert (> x 0))\n" * 200, tmp_path)
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")
    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is False
    assert "preamble" in result["reason"]


def test_records_the_absence_when_dafny_writes_nothing(monkeypatch, tmp_path):
    """The failure mode that caused the fabrication: three documented ways of
    asking Dafny for a proof log report success and create zero files."""
    gen = _generator_writing(monkeypatch, None, tmp_path)
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")

    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is False
    assert result["path"] is None
    assert result["reason"], "a missing artifact must say why"


def test_a_stale_artifact_cannot_be_mistaken_for_this_run(monkeypatch, tmp_path):
    """A previous run's file left in place would be hashed as if it were new."""
    target = tmp_path / "spec.dfy"
    target.write_text("method M() {}")
    stale = tmp_path / "spec.smt2"
    stale.write_text("(set-option :old)\n" + "(assert (> x 0))\n" * 200)

    gen = _generator_writing(monkeypatch, None, tmp_path)   # dafny writes nothing
    result = gen._emit_proof_artifact(str(target))
    assert result["replayable"] is False
    assert not stale.exists(), "the previous run's artifact survived"
