import os
import pytest
from scripts.receipt_generator import ReceiptGenerator, verify_receipt


def test_receipt_generation_and_audit(tmp_path):
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
