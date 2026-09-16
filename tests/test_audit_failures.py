"""The audit's failure findings -- the lines that decide a receipt is bad.

Every existing audit test exercises a receipt that PASSES. That is the wrong
half to test exhaustively: an audit that can only be observed passing is
indistinguishable from one that always passes, which is precisely the defect
this file's subject (`audit_receipt`) was written to correct after an earlier
version printed three checkmarks while validating one thing.

So each test here breaks exactly one of the three artifacts a receipt makes a
claim about -- itself, the source, the proof -- and asserts the audit says so.
"""

import json
import os

import pytest

from scripts import receipt_generator
from scripts.receipt_generator import audit_receipt, recompute_seal


def _sealed_receipt(tmp_path, **overrides):
    """A minimal receipt that passes, so each test's single break is the only
    difference between passing and failing."""
    source = tmp_path / "unit.dfy"
    source.write_text("method M() {}\n")
    data = {
        "source_file": str(source),
        "formal_verification": {"verified": True},
        "proof_artifact": {"smt2_file": None, "reason": "none emitted"},
        "hashes": {
            "dafny_source_sha256": receipt_generator.compute_file_sha256(str(source)),
            "smt2_proof_sha256": None,
        },
    }
    data.update(overrides)
    data["receipt_seal_sha256"] = recompute_seal(data)
    path = tmp_path / "r.receipt.json"
    path.write_text(json.dumps(data))
    return path, data


def _audit(path, replay=True):
    return audit_receipt(str(path), replay=replay)


def test_a_receipt_with_no_seal_fails(tmp_path):
    """An unsealed receipt cannot be shown to be unedited. Treating a missing
    seal as 'nothing to compare against, so fine' would let anyone strip the
    field and pass."""
    path, data = _sealed_receipt(tmp_path)
    del data["receipt_seal_sha256"]
    path.write_text(json.dumps(data))
    result = _audit(path)
    assert not result["passed"]
    assert any("no seal" in f for f in result["failures"])


def test_a_receipt_whose_source_vanished_fails(tmp_path):
    """The receipt attests to a file. With the file gone there is nothing left
    to attest to, and the hash it records cannot be confirmed against
    anything."""
    path, data = _sealed_receipt(tmp_path)
    os.remove(data["source_file"])
    result = _audit(path)
    assert not result["passed"]
    assert any("source file not found" in f for f in result["failures"])


def test_a_receipt_naming_a_proof_that_is_not_there_fails(tmp_path):
    """Claiming an artifact and not having one is worse than claiming none:
    the receipt reads as proof-backed while nothing can be replayed."""
    path, _ = _sealed_receipt(
        tmp_path,
        proof_artifact={"smt2_file": str(tmp_path / "gone.smt2")},
    )
    result = _audit(path)
    assert not result["passed"]
    assert any("missing" in f for f in result["failures"])


def test_a_proof_that_does_not_replay_fails_even_though_its_hash_matches(tmp_path, toolchain):
    """The case the hash check cannot reach.

    The artifact is exactly the one archived -- its hash matches perfectly --
    and it still does not hold. A `sat` answer means an obligation was
    satisfiable in its negated form. Only re-running distinguishes an archived
    proof from an archived file.
    """
    smt2 = tmp_path / "bad.smt2"
    smt2.write_text("(declare-const x Int)\n(assert (> x 0))\n(check-sat)\n")
    path, _ = _sealed_receipt(
        tmp_path,
        proof_artifact={"smt2_file": str(smt2)},
        hashes={"smt2_proof_sha256": receipt_generator.compute_file_sha256(str(smt2))},
    )
    # Re-seal: the overrides above replaced the hashes block wholesale.
    data = json.loads(path.read_text())
    data["hashes"]["dafny_source_sha256"] = receipt_generator.compute_file_sha256(
        data["source_file"])
    data["receipt_seal_sha256"] = recompute_seal(
        {k: v for k, v in data.items() if k != "receipt_seal_sha256"})
    path.write_text(json.dumps(data))

    result = _audit(path)
    assert any("does NOT replay" in f for f in result["failures"]), (
        "a matching hash was accepted as a holding proof: %r" % (result,))


def test_an_unreplayable_proof_is_a_note_not_a_pass_and_not_a_failure(tmp_path, monkeypatch):
    """Three outcomes, not two. 'Could not check' must not collapse into
    either 'checked and fine' or 'checked and broken'."""
    smt2 = tmp_path / "p.smt2"
    smt2.write_text("(check-sat)\n")
    monkeypatch.setattr(receipt_generator, "replay_proof",
                        lambda *a, **k: {"checked": False, "reason": "no solver"})
    path, _ = _sealed_receipt(tmp_path, proof_artifact={"smt2_file": str(smt2)})
    data = json.loads(path.read_text())
    data["hashes"]["smt2_proof_sha256"] = receipt_generator.compute_file_sha256(str(smt2))
    data["receipt_seal_sha256"] = recompute_seal(
        {k: v for k, v in data.items() if k != "receipt_seal_sha256"})
    path.write_text(json.dumps(data))

    result = _audit(path)
    assert result["notes"], "an unchecked proof produced no note"
    assert not any("does NOT replay" in f for f in result["failures"]), \
        "'nothing ran' was reported as a proof failure"


def test_requires_false_is_caught_without_a_solver(tmp_path):
    """Shape A in its canonical form.

    `requires false` needs neither a solver nor a parser to recognise, and the
    check for it ran ahead of both -- untested until now, which meant the
    textbook case of the defect this tool is named for was the one case never
    exercised.
    """
    from scripts.spec_scorer import SpecScorer

    dfy = tmp_path / "unreachable.dfy"
    dfy.write_text("""
    method Never(x: int) returns (y: int)
      requires false
      ensures y == x + 1
    { y := x - 1000; }
    """)
    res = SpecScorer(str(dfy)).analyze()
    assert res["overall_score"] <= 30
    assert any(g["status"] == "CONTRADICTION" for g in res["methods"][0]["gaps"])
