"""The command-line surfaces, exercised in-process so they are measured.

These are what a user and an agent actually touch. One of them shipped a bug
that only the CLI could have: `receipt create` printed a path built by
appending ".receipt.json" to the full source path, producing
"foo.dfy.receipt.json" while generate() had written "foo.receipt.json" --
so following the printed path hit a file-not-found on an artifact that
existed. The library was correct and the report was not.
"""

import json
import sys

import pytest

from scripts import check_env as env_mod
from scripts import compiler as compiler_mod
from scripts import receipt_generator as receipt_mod
from scripts import spec_scorer as scorer_mod
from scripts import verify_loop as verify_mod


def _run(module, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", argv)
    try:
        module.main()
    except SystemExit as exit_info:
        return exit_info.code if exit_info.code is not None else 0
    return 0


# --- receipt CLI --------------------------------------------------------

def test_receipt_create_prints_the_path_it_actually_wrote(tmp_path, monkeypatch, capsys):
    """The regression above, asserted by opening what was printed."""
    source = tmp_path / "unit.dfy"
    source.write_text("method M() {}\n")

    monkeypatch.setattr(receipt_mod.ReceiptGenerator, "_emit_proof_artifact",
                        lambda self, p: {"path": None, "sha256": None,
                                         "replayable": False, "assertion_count": 0,
                                         "reason": "stubbed for this test"})
    monkeypatch.setattr(receipt_mod.DafnyVerifier, "verify",
                        lambda self, p, **kw: {"verified": False, "status": "TOOLCHAIN_ERROR",
                                               "raw_output": "", "errors": []})

    code = _run(receipt_mod, ["receipt_generator.py", "create", str(source)], monkeypatch)
    out = capsys.readouterr().out
    printed = [ln.split(":", 1)[1].strip() for ln in out.splitlines()
               if ln.startswith("✓ Receipt generated:")]
    assert printed, out
    with open(printed[0]) as handle:
        json.load(handle)          # the printed path must be openable
    assert code == 0


def test_receipt_create_says_plainly_when_there_is_no_proof(tmp_path, monkeypatch, capsys):
    """A receipt with nothing behind it is still useful, but only if it does
    not read like one that has a proof."""
    source = tmp_path / "unit.dfy"
    source.write_text("method M() {}\n")
    monkeypatch.setattr(receipt_mod.ReceiptGenerator, "_emit_proof_artifact",
                        lambda self, p: {"path": None, "sha256": None,
                                         "replayable": False, "assertion_count": 0,
                                         "reason": "dafny not available"})
    monkeypatch.setattr(receipt_mod.DafnyVerifier, "verify",
                        lambda self, p, **kw: {"verified": False, "status": "TOOLCHAIN_ERROR",
                                               "raw_output": "", "errors": []})
    _run(receipt_mod, ["receipt_generator.py", "create", str(source)], monkeypatch)
    out = capsys.readouterr().out
    assert "Proof       : NONE" in out
    assert "dafny not available" in out


# --- compiler CLI -------------------------------------------------------

def test_compile_cli_reports_success_with_the_output_directory(tmp_path, monkeypatch, capsys):
    source = tmp_path / "u.dfy"
    source.write_text("method Main() {}\n")
    monkeypatch.setattr(compiler_mod.DafnyCompiler, "compile",
                        lambda self, p, **kw: {"success": True, "target": "py",
                                               "output_dir": str(tmp_path)})
    code = _run(compiler_mod, ["compiler.py", str(source)], monkeypatch)
    out = capsys.readouterr().out
    assert code == 0
    assert "Successfully compiled" in out and str(tmp_path) in out


def test_compile_cli_surfaces_the_toolchain_output_on_failure(tmp_path, monkeypatch, capsys):
    """A bare "compilation failed" sends the reader back to the terminal to
    re-run it by hand. The raw output is the part that says why."""
    source = tmp_path / "u.dfy"
    source.write_text("method Main() {}\n")
    monkeypatch.setattr(compiler_mod.DafnyCompiler, "compile",
                        lambda self, p, **kw: {"success": False, "error": "target unsupported",
                                               "raw_output": "DISTINCTIVE_TOOLCHAIN_LINE"})
    code = _run(compiler_mod, ["compiler.py", str(source)], monkeypatch)
    err = capsys.readouterr().err
    assert code == 1
    assert "target unsupported" in err and "DISTINCTIVE_TOOLCHAIN_LINE" in err


def test_compile_cli_rejects_a_missing_file(monkeypatch, capsys):
    code = _run(compiler_mod, ["compiler.py", "/nonexistent/u.dfy"], monkeypatch)
    assert code == 1
    assert "not found" in capsys.readouterr().err


# --- verify CLI ---------------------------------------------------------

def test_verify_cli_json_is_machine_readable(tmp_path, monkeypatch, capsys):
    """An agent branches on this. It has to parse, and it has to carry the
    status the exit code was derived from."""
    source = tmp_path / "u.dfy"
    source.write_text("method M() {}\n")
    monkeypatch.setattr(verify_mod.DafnyVerifier, "verify",
                        lambda self, p, **kw: {"status": "PROVED", "verified": True,
                                               "errors": [], "raw_output": ""})
    code = _run(verify_mod, ["verify_loop.py", str(source), "--json"], monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PROVED"
    assert code == 0


def test_verify_cli_vacuous_is_not_reported_as_proved(tmp_path, monkeypatch, capsys):
    """The four-state verdict, end to end through the CLI. VACUOUS_PROOF has
    its own exit code precisely so it cannot be read as success."""
    source = tmp_path / "u.dfy"
    source.write_text("method M() {}\n")
    monkeypatch.setattr(verify_mod.DafnyVerifier, "verify",
                        lambda self, p, **kw: {"status": "VACUOUS_PROOF", "verified": False,
                                               "errors": [], "raw_output": ""})
    code = _run(verify_mod, ["verify_loop.py", str(source)], monkeypatch)
    assert code == 3, "a vacuous proof shared an exit code with a real one"


# --- env CLI ------------------------------------------------------------

def test_env_cli_names_the_solver_it_found_on_path(monkeypatch, capsys):
    """Which z3 is in use decides whether a proof means anything, so the
    report says which one and where it came from."""
    monkeypatch.delenv("VERICODING_Z3", raising=False)
    monkeypatch.setattr(env_mod, "probe_verification", lambda: {"ok": True})
    monkeypatch.setattr(env_mod, "check_tool", lambda name, *a, **k: {
        "installed": True,
        "path": "/distinctive/path/z3" if name == "z3" else "/usr/bin/" + name,
        "version": "4.x"})
    code = _run(env_mod, ["check_env.py", "--no-targets"], monkeypatch)
    out = capsys.readouterr().out
    assert "/distinctive/path/z3" in out
    assert code == 0


# --- parser edges -------------------------------------------------------

def test_an_escaped_quote_does_not_end_a_string(tmp_path):
    """`"\\""` is one string containing a quote. Treating the escaped quote as
    the terminator resumes "code" scanning inside a literal, and the next `//`
    in that literal starts eating real clauses."""
    from scripts.spec_scorer import strip_comments
    source = r'''method M() { print "he said \" // not a comment"; }'''
    assert "// not a comment" in strip_comments(source)


def test_a_method_with_no_body_is_still_parsed(tmp_path):
    """Contract stubs have no braces at all. _extract_body must return empty
    rather than running off the end of the file."""
    from scripts.spec_scorer import DafnySpecParser
    methods = DafnySpecParser("""
    method Stub(x: int) returns (y: int)
      requires x > 0
      ensures y == x
    """).methods
    assert len(methods) == 1 and methods[0]["name"] == "Stub"


def test_receipt_create_prints_how_to_replay_the_proof(tmp_path, monkeypatch, capsys):
    """The line that makes a receipt worth more than an assertion.

    The whole claim of a proof artifact is that a third party can re-check it
    without trusting this pipeline, this machine, or the model that wrote the
    code. That is only true if the receipt tells them how. Printing the
    artifact path and assertion count without the replay command leaves the
    reader with a file and no verb.
    """
    source = tmp_path / "unit.dfy"
    source.write_text("method M() {}\n")
    artifact = tmp_path / "unit.smt2"
    artifact.write_text("(assert (and (> 1 0) (< 1 0)))\n(check-sat)\n")

    monkeypatch.setattr(receipt_mod.ReceiptGenerator, "_emit_proof_artifact",
                        lambda self, p: {"path": str(artifact),
                                         "sha256": receipt_mod.compute_file_sha256(str(artifact)),
                                         "replayable": True, "assertion_count": 7})
    monkeypatch.setattr(receipt_mod.DafnyVerifier, "verify",
                        lambda self, p, **kw: {"verified": True, "status": "PROVED",
                                               "raw_output": "", "errors": []})

    _run(receipt_mod, ["receipt_generator.py", "create", str(source)], monkeypatch)
    out = capsys.readouterr().out
    assert str(artifact) in out
    assert "7 assertions" in out
    assert "Replay with" in out, "a proof was announced with no way to check it"


def test_env_probe_verifies_rather_than_asking_for_a_version(monkeypatch):
    """`--version` answers a question nobody asked. Whether Dafny can complete
    a verification on this host is the thing that decides if a proof means
    anything, and only running one answers it.
    """
    seen = {}

    def record(args, timeout):
        seen["args"] = args
        return {"ok": True}

    monkeypatch.setattr(env_mod, "_run_probe", record)
    assert env_mod.probe_verification()["ok"] is True
    assert seen["args"] == ["verify"], (
        "the probe asked something other than 'can you verify': %r" % (seen,))


def test_an_empty_clause_body_is_skipped_not_recorded(tmp_path):
    """`requires ;` contributes nothing. Recording it as a clause would let a
    spec collect precondition credit for punctuation."""
    from scripts.spec_scorer import DafnySpecParser
    methods = DafnySpecParser("""
    method M(x: int) returns (y: int)
      requires ;
      requires x > 0
      ensures y == x
    { y := x; }
    """).methods
    assert methods[0]["requires"] == ["x > 0"]
