"""The end-to-end pipeline's specification gate.

`cmd_pipeline` used to run the scorer, print the result, and never read it
again. That made the scorer decorative in the one command where it matters
most. Measured before the gate, end to end, exit 0:

    Spec Confidence: 15.0% [LOW]
    ✓ Formal verification succeeded!
    ✓ Cryptographic Receipt Seal: 9219c342...
    >>> Vericoding Pipeline Completed Successfully! Math, Not Vibes. <<<

The specification there has one postcondition, `n < 0 ==> !ok` under
`requires n >= 0`, which can never fire, and an implementation that ignores
its input. No verifier can see that shape -- the clause is honestly true --
so the scorer was the only thing between it and a signed receipt.
"""

import json
import subprocess
import sys
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CLI = str(REPO / "bin" / "vericoding")

VACUOUS = """
method Check(n: int) returns (ok: bool)
  requires n >= 0
  ensures n < 0 ==> !ok
{
  ok := true;
}
"""


def _run(args):
    return subprocess.run(
        [sys.executable, CLI] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(REPO), timeout=300,
    )


def test_pipeline_refuses_a_vacuous_spec(tmp_path):
    """No toolchain needed: the gate fires before verification is attempted."""
    spec = tmp_path / "vacuous.dfy"
    spec.write_text(VACUOUS)
    proc = _run(["pipeline", str(spec), "--out", str(tmp_path / "out")])

    assert proc.returncode == 1, proc.stdout
    assert "vacuous clause(s) found" in proc.stdout
    assert "n < 0 ==> !ok" in proc.stdout
    assert "Completed Successfully" not in proc.stdout
    # Nothing downstream may run, least of all the thing that issues receipts.
    assert not (tmp_path / "vacuous.receipt.json").exists()


def test_vacuity_blocks_regardless_of_threshold(tmp_path):
    """A clause that cannot fire protects nothing at any score, so lowering
    --min-score must not buy past it."""
    spec = tmp_path / "vacuous.dfy"
    spec.write_text(VACUOUS)
    proc = _run(["pipeline", str(spec), "--min-score", "0", "--out", str(tmp_path / "out")])
    assert proc.returncode == 1, proc.stdout
    assert "vacuous clause(s) found" in proc.stdout


def test_force_records_the_override_in_the_receipt(tmp_path, toolchain):
    spec = tmp_path / "vacuous.dfy"
    spec.write_text(VACUOUS)
    proc = _run(["pipeline", str(spec), "--force", "--reviewed", "--out", str(tmp_path / "out")])
    assert proc.returncode == 0, proc.stdout

    receipt = json.loads((tmp_path / "vacuous.receipt.json").read_text())
    gate = receipt["spec_gate"]
    assert gate["overridden"] is True
    assert gate["passed"] is False
    assert gate["vacuous_clauses"] == ["n < 0 ==> !ok"]
    # Without this a forced receipt reads identically to an earned one, and
    # the forced case is precisely what a reader needs to know about.
    assert gate["score"] < gate["min_score"]


def test_a_sound_spec_passes_the_gate(tmp_path, toolchain):
    """Runs against a COPY of the example.

    Pointing the pipeline at examples/ rewrites the committed receipt on every
    run: `git status` is never clean, a real change hides in the churn, and an
    interrupted run can leave the receipt inconsistent with the .smt2 beside
    it -- which produced four false failures before this was fixed.
    """
    spec = tmp_path / "bank.dfy"
    spec.write_text((REPO / "examples/bank_account/bank.dfy").read_text())

    proc = _run(["pipeline", str(spec), "--reviewed", "--out", str(tmp_path / "out")])
    assert proc.returncode == 0, proc.stdout
    assert "Completed Successfully" in proc.stdout

    receipt = json.loads((tmp_path / "bank.receipt.json").read_text())
    assert receipt["spec_gate"]["passed"] is True
    assert receipt["spec_gate"]["overridden"] is False


def test_pipeline_stops_for_human_review(tmp_path):
    """Methodology stage 4, which the README advertised and the code skipped.

    The pipeline ran score -> verify -> compile -> receipt straight through.
    Every check in that chain answers "does the implementation satisfy the
    spec"; none can answer "is this the right spec", and no solver ever will.
    A clause that says the wrong thing verifies exactly as cleanly as one that
    says the right thing.

    No toolchain needed: the stop happens before verification.
    """
    spec = tmp_path / "bank.dfy"
    spec.write_text((REPO / "examples/bank_account/bank.dfy").read_text())
    proc = _run(["pipeline", str(spec), "--out", str(tmp_path / "out")])
    assert proc.returncode == 3, proc.stdout
    assert "Stopping for review" in proc.stdout
    assert "review_checklist.md" in proc.stdout
    assert "Completed Successfully" not in proc.stdout


def test_reviewed_flag_continues_and_is_recorded(tmp_path, toolchain):
    """The attestation is recorded, not verified -- the tool cannot know
    whether anyone read anything. Recording it still beats not, because a
    receipt that omits it reads the same either way."""
    import json as _json

    spec = tmp_path / "bank.dfy"
    spec.write_text((REPO / "examples/bank_account/bank.dfy").read_text())
    proc = _run(["pipeline", str(spec), "--reviewed", "--out", str(tmp_path / "out")])
    assert proc.returncode == 0, proc.stdout
    assert "Review attested by the caller" in proc.stdout

    receipt = _json.loads((tmp_path / "bank.receipt.json").read_text())
    assert receipt["spec_gate"]["human_review_attested"] is True
