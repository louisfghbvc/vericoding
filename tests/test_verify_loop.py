import os
import pytest
from scripts.verify_loop import DafnyVerifier


def test_verifier_pass():
    verifier = DafnyVerifier()
    if not verifier.is_available():
        pytest.skip("Dafny CLI not installed")

    res = verifier.verify("examples/bank_account/bank.dfy")
    assert res["success"] is True
    assert res["verified"] is True
    assert len(res["diagnostics"]) == 0


def test_verifier_failure(tmp_path):
    verifier = DafnyVerifier()
    if not verifier.is_available():
        pytest.skip("Dafny CLI not installed")

    failing_dfy = tmp_path / "fail.dfy"
    failing_dfy.write_text("""
    method Wrong(x: int) returns (y: int)
        ensures y == x + 10
    {
        y := x + 1;
    }
    """)
    res = verifier.verify(str(failing_dfy))
    assert res["success"] is True
    assert res["verified"] is False
    assert len(res["diagnostics"]) > 0
    assert any("postcondition" in d["message"].lower() for d in res["diagnostics"])
