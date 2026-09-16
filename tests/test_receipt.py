import json
import os

from scripts.receipt_generator import ReceiptGenerator, verify_receipt


def test_receipt_generation_and_audit(tmp_path, toolchain):
    rg = ReceiptGenerator()
    receipt_file = tmp_path / "test.receipt.json"

    res = rg.generate(
        "examples/bank_account/bank.dfy",
        nl_intent="Bank account withdrawal spec",
        output_receipt_path=str(receipt_file),
        dump_smt=False
    )
    assert res["formal_verification"]["verified"] is True
    assert "receipt_seal_sha256" in res
    assert os.path.exists(str(receipt_file))

    # Audit the receipt
    is_valid = verify_receipt(str(receipt_file))
    assert is_valid is True


def _clean_receipt():
    return "examples/bank_account/bank.receipt.json"


def test_audit_accepts_an_untampered_receipt():
    assert verify_receipt(_clean_receipt()) is True


def test_audit_rejects_a_forged_seal(tmp_path):
    """The seal used to be printed but never recomputed.

    Replacing it with 64 zeros produced a green "Receipt Seal: 0000..." line
    and exit 0, so the one field whose whole purpose is tamper-evidence was
    decorative.
    """
    forged = tmp_path / "forged.receipt.json"
    data = json.loads(open(_clean_receipt()).read())
    data["receipt_seal_sha256"] = "0" * 64
    forged.write_text(json.dumps(data, indent=2))
    assert verify_receipt(str(forged)) is False


def test_audit_rejects_an_edited_field(tmp_path):
    """Any edit breaks the seal, including one that looks like a hash."""
    forged = tmp_path / "edited.receipt.json"
    data = json.loads(open(_clean_receipt()).read())
    data["hashes"]["smt2_proof_sha256"] = "deadbeef" * 8
    forged.write_text(json.dumps(data, indent=2))
    assert verify_receipt(str(forged)) is False


def test_audit_rejects_a_swapped_proof_artifact(tmp_path):
    """The case the seal cannot catch: receipt untouched, artifact replaced.

    This is precisely the stub the generator used to fabricate -- no
    assertions, re-checks as `sat`. Without a per-artifact hash check a
    receipt would keep vouching for a proof that had been swapped out from
    under it.
    """
    import shutil

    data = json.loads(open(_clean_receipt()).read())
    artifact = data["proof_artifact"]["smt2_file"]
    backup = tmp_path / "artifact.bak"
    shutil.copy(artifact, str(backup))
    try:
        with open(artifact, "w") as f:
            f.write("; stub\n(set-logic ALL)\n(check-sat)\n")
        assert verify_receipt(_clean_receipt()) is False
    finally:
        shutil.copy(str(backup), artifact)

    # and the restored artifact verifies again, so the test left no damage
    assert verify_receipt(_clean_receipt()) is True


def test_replay_accepts_a_real_proof(toolchain):
    """The property the whole method rests on, actually exercised.

    The artifact is portable SMT-LIB2, so anyone can check it without
    trusting this pipeline, this machine, or the model that wrote the code.
    Until something runs it, `replayable: true` is a claim about a file
    nobody has opened.
    """
    from scripts.receipt_generator import replay_proof

    result = replay_proof("examples/bank_account/bank.smt2")
    assert result["checked"] is True, result.get("reason")
    assert result["ok"] is True, result.get("reason")
    assert result["checks"] > 0


def test_replay_rejects_the_stub_the_generator_used_to_fabricate(tmp_path, toolchain):
    """The hash check catches a swapped artifact, but only because the hash
    changed. This is the other half: a file whose hash is whatever it is, and
    whose contents prove nothing.

    Five lines, no assertions, and it answers `sat` because an empty query is
    trivially satisfiable -- which is why the old receipts' `replayable: true`
    was never contradicted by anything.
    """
    from scripts.receipt_generator import replay_proof

    stub = tmp_path / "stub.smt2"
    stub.write_text("; SMT-LIB2 Verification Proof Artifact\n(set-logic ALL)\n(check-sat)\n")
    result = replay_proof(str(stub))
    assert result["checked"] is True
    assert result["ok"] is False
    assert "sat" in result["reason"]


def test_audit_reports_an_unreplayable_proof_as_neither_pass_nor_fail(tmp_path, monkeypatch):
    """No solver is not a verdict.

    Reporting it as a pass would let an unchecked proof read as a checked
    one; reporting it as a failure would blame the receipt for the host. It
    is a third state, and `audit` exits 2 for it.
    """
    from scripts import receipt_generator

    monkeypatch.setattr(
        receipt_generator, "replay_proof",
        lambda path, solver_path=None: {"checked": False, "reason": "no z3"},
    )
    result = receipt_generator.audit_receipt("examples/bank_account/bank.receipt.json")
    assert result["passed"] is True          # nothing checked was wrong
    assert result["notes"], "an unrunnable check must be reported, not silently dropped"
    assert not result["failures"]
