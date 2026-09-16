"""The CLI entry points, and the replay failure paths behind `audit`.

`main()` is what a user and a CI job actually meet, and both branch on its
exit code. Driving it as a subprocess exercises it but leaves coverage blind,
so these call it in-process with a patched argv and catch the SystemExit --
the code under test is the same either way, and this way it is measured.

`replay_proof` is the check that distinguishes "this file is the one that was
archived" from "the proof inside it holds". When it cannot run, it has to say
so rather than return either verdict.
"""

import subprocess
import sys

import pytest

from scripts import compiler as compiler_mod
from scripts import receipt_generator as receipt_mod
from scripts.receipt_generator import replay_proof


def _run_main(module, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    return exit_info.value.code


# --- replay_proof -------------------------------------------------------

@pytest.mark.parametrize("case", ["missing artifact", "missing solver", "timeout"])
def test_an_unrun_replay_carries_no_verdict_at_all(case, tmp_path, monkeypatch):
    """`ok` is absent, not False, when nothing ran.

    That is the contract and it is deliberate: a caller reading ok=False would
    see "the proof failed" where the truth is "nothing was checked", and those
    route differently -- audit_receipt makes the first a failure and the second
    a note. Asserting the key is missing locks the distinction in, because
    adding `ok: False` here is the obvious and wrong tidy-up.
    """
    artifact = tmp_path / "p.smt2"
    if case == "missing artifact":
        target = str(tmp_path / "absent.smt2")
    else:
        artifact.write_text("(declare-const x Int)\n(assert (> x 0))\n(check-sat)\n")
        target = str(artifact)

    if case == "missing solver":
        monkeypatch.setattr(receipt_mod.shutil, "which", lambda name: None)
        monkeypatch.delenv("VERICODING_Z3", raising=False)
    elif case == "timeout":
        monkeypatch.setattr(receipt_mod.shutil, "which", lambda name: "/usr/bin/z3")
        monkeypatch.setenv("VERICODING_Z3", "/usr/bin/z3")
        monkeypatch.setattr(
            receipt_mod.subprocess, "run",
            lambda cmd, **kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd, receipt_mod.REPLAY_TIMEOUT_S)))

    result = replay_proof(target)
    assert result["checked"] is False
    assert "ok" not in result, "an unrun replay must not carry a verdict"
    assert result["reason"]


# --- compiler CLI -------------------------------------------------------

def test_compile_cli_rejects_a_missing_file(monkeypatch, capsys):
    code = _run_main(compiler_mod, ["compiler.py", "/nonexistent/x.dfy"], monkeypatch)
    assert code == 1
    assert "file not found" in capsys.readouterr().err


def test_compile_cli_lists_valid_targets_when_one_is_wrong(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "x.dfy"
    spec.write_text("method M() {}")
    code = _run_main(
        compiler_mod,
        ["compiler.py", str(spec), "--target", "rust", "--out", str(tmp_path / "o")],
        monkeypatch)
    err = capsys.readouterr().err
    assert code == 1
    assert "Unsupported target" in err
    for spelling in ("py", "go", "js", "cs"):
        assert spelling in err, "a rejection that does not say what IS valid is half a message"


def test_compile_cli_surfaces_an_unexpected_exception(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "x.dfy"
    spec.write_text("method M() {}")
    dafny = tmp_path / "dafny"
    dafny.write_text("#!/bin/sh\n")
    monkeypatch.setattr(compiler_mod.shutil, "which", lambda name: str(dafny))
    monkeypatch.setattr(
        compiler_mod.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("Exec format error")))
    code = _run_main(
        compiler_mod, ["compiler.py", str(spec), "--out", str(tmp_path / "o")], monkeypatch)
    assert code == 1
    assert "Exec format error" in capsys.readouterr().err


# --- receipt CLI --------------------------------------------------------

def test_audit_cli_exits_non_zero_on_a_tampered_receipt(tmp_path, monkeypatch, capsys):
    import json

    source = tmp_path / "s.dfy"
    source.write_text("method M() {}")
    receipt = tmp_path / "s.receipt.json"
    receipt.write_text(json.dumps({
        "source_file": str(source),
        "hashes": {"dafny_source_sha256": "0" * 64, "smt2_proof_sha256": None},
        "formal_verification": {"verified": True},
        "proof_artifact": {"smt2_file": None, "replayable": False, "reason": "none"},
        "receipt_seal_sha256": "0" * 64,
    }))
    code = _run_main(
        receipt_mod, ["receipt_generator.py", "audit", str(receipt)], monkeypatch)
    assert code == 1
    out = capsys.readouterr().out
    assert "✗" in out


def test_audit_cli_accepts_a_receipt_it_just_issued(tmp_path, monkeypatch, capsys):
    source = tmp_path / "s.dfy"
    source.write_text("method M() {}")
    gen = receipt_mod.ReceiptGenerator(dafny_path="/nonexistent/dafny")
    gen.generate(str(source), output_receipt_path=str(tmp_path / "s.receipt.json"))

    code = _run_main(
        receipt_mod,
        ["receipt_generator.py", "audit", str(tmp_path / "s.receipt.json")],
        monkeypatch)
    out = capsys.readouterr().out
    assert code == 0, out
    # It has to say there is no proof, not stay silent about it.
    assert "No proof artifact claimed" in out
