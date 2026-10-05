""""I could not check this" must not be reported as "this spec scores badly".

Vacuity analysis needs the z3 Python bindings (`pip install z3-solver`) --
a different thing from the z3 executable. When they are absent the analysis
used to degrade into the score: every implication postcondition and every
precondition satisfiability check became a NOT ANALYSED gap worth -20, and
the tool printed a lower percentage. Measured on the bundled example before
this change:

    with bindings     84.0% [MODERATE]
    without bindings  72.0% [MODERATE]

Same specification, same clauses, twelve points apart -- and the difference
is a missing package on the reader's machine, not a defect in their spec. A
spec whose methods lean harder on implications loses more, and the default
`--min-score 60` then turns "this tool is not installed" into "your
specification is poor, the pipeline refuses it".

That is the same failure shape this repository already shipped once and
fixed: an untampered receipt reported as failing audit, which reads as "this
proof was tampered with". Both render *I don't know* as *you are wrong*, and
both send the reader to debug something that was never broken.

So the analysis has three outcomes, not a number: analysed, could-not-analyse
(with the reason and the remedy), or failed. The gate refuses on anything but
the first, and says what to install.
"""

import pathlib
import subprocess
import sys

import pytest

from scripts import spec_scorer as scorer_mod
from scripts.spec_scorer import SpecScorer, format_cli_report

REPO = pathlib.Path(__file__).resolve().parent.parent
CLI = str(REPO / "bin" / "vericoding")

# Nothing wrong with this specification. Two live postconditions, two
# preconditions, an implication the solver would have to reason about.
SOUND = """
method Withdraw(balance: int, amount: int) returns (ok: bool, newBalance: int)
  requires amount > 0
  requires balance >= amount
  ensures ok ==> newBalance == balance - amount
  ensures !ok ==> newBalance == balance
{
  ok := true;
  newBalance := balance - amount;
}
"""


def _analyse(tmp_path, source=SOUND):
    path = tmp_path / "s.dfy"
    path.write_text(source)
    return SpecScorer(str(path)).analyze()


def _without_bindings(monkeypatch):
    """Exactly how the module sees an absent package: `import z3` failed."""
    monkeypatch.setattr(scorer_mod, "z3", None)


# --- the analysis state -------------------------------------------------

def test_missing_bindings_are_a_distinct_state_not_a_lower_score(tmp_path, monkeypatch):
    _without_bindings(monkeypatch)
    res = _analyse(tmp_path)

    vacuity = res["vacuity_analysis"]
    assert vacuity["status"] == scorer_mod.VACUITY_UNAVAILABLE
    assert "z3" in vacuity["reason"].lower()
    assert vacuity["remedy"] == scorer_mod.Z3_INSTALL_HINT
    assert res["score_complete"] is False


def test_an_unanalysable_score_is_not_presented_as_a_verdict(tmp_path, monkeypatch):
    """The number is a floor computed without the check, so it must not be
    printed in the place a reader reads as a quality judgement."""
    _without_bindings(monkeypatch)
    res = _analyse(tmp_path)
    out = format_cli_report(res)

    assert "NOT ESTABLISHED" in out
    assert scorer_mod.Z3_INSTALL_HINT in out
    assert "%s%% [" % res["overall_score"] not in out, \
        "a score computed without the vacuity check was printed as a confidence verdict"
    assert res["confidence_level"] == scorer_mod.CONFIDENCE_NOT_ESTABLISHED


@pytest.mark.skipif(scorer_mod.z3 is None, reason="z3 bindings not installed here")
def test_a_working_analysis_is_still_reported_as_analysed(tmp_path):
    """The other half of the contract: nothing about the analysed path moves."""
    res = _analyse(tmp_path)
    assert res["vacuity_analysis"]["status"] == scorer_mod.VACUITY_ANALYSED
    assert res["score_complete"] is True
    assert res["confidence_level"] != scorer_mod.CONFIDENCE_NOT_ESTABLISHED
    assert isinstance(res["overall_score"], float)


def test_a_crashed_solver_is_failed_not_merely_analysed(tmp_path, monkeypatch):
    """A solver that threw did not answer the question either, and the three
    outcomes have to tell that apart from a package that was never there."""
    if scorer_mod.z3 is None:
        pytest.skip("z3 bindings not installed here")

    class _ExplodingSolver:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("solver blew up mid-analysis")

    monkeypatch.setattr(scorer_mod.z3, "Solver", _ExplodingSolver)
    res = _analyse(tmp_path)
    assert res["vacuity_analysis"]["status"] == scorer_mod.VACUITY_FAILED
    assert res["score_complete"] is False


# --- the gate -----------------------------------------------------------


def _cli(args, tmp_path, block_z3, merge_stderr=True):
    """Run the CLI, optionally with the z3 bindings made unimportable.

    A stub module that raises ImportError is what an uninstalled package
    looks like to `import z3`, and it is the only mechanism that also covers
    the subprocess the CLI actually runs.
    """
    env = {k: v for k, v in __import__("os").environ.items()}
    if block_z3:
        stub = tmp_path / "noz3"
        stub.mkdir(exist_ok=True)
        (stub / "z3.py").write_text(
            'raise ImportError("No module named \'z3\' (simulated)")\n')
        env["PYTHONPATH"] = str(stub) + ":" + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, CLI] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True, cwd=str(REPO), timeout=300, env=env,
    )


def test_pipeline_refuses_when_the_vacuity_check_could_not_run(tmp_path):
    spec = tmp_path / "sound.dfy"
    spec.write_text(SOUND)
    proc = _cli(["pipeline", str(spec), "--reviewed", "--out", str(tmp_path / "out")],
                tmp_path, block_z3=True)

    assert proc.returncode != 0, proc.stdout
    assert scorer_mod.Z3_INSTALL_HINT in proc.stdout
    assert "could not" in proc.stdout.lower()
    # The refusal must not read as a judgement on the specification.
    assert "below the --min-score" not in proc.stdout
    assert "Completed Successfully" not in proc.stdout
    assert not (tmp_path / "sound.receipt.json").exists()


def test_force_does_not_buy_past_an_unrun_vacuity_check(tmp_path):
    """--force means "I have read these findings about my spec and accept
    them". There are no findings here; there is no evidence at all."""
    spec = tmp_path / "sound.dfy"
    spec.write_text(SOUND)
    proc = _cli(["pipeline", str(spec), "--reviewed", "--force",
                 "--out", str(tmp_path / "out")], tmp_path, block_z3=True)
    assert proc.returncode != 0, proc.stdout
    assert scorer_mod.Z3_INSTALL_HINT in proc.stdout


def test_score_exits_with_its_own_code_when_it_could_not_analyse(tmp_path):
    """"It scored" and "it could not be scored" are different facts, and the
    exit code has to say which -- the same split `audit` already makes with
    its 2 for "a check could not be run"."""
    spec = tmp_path / "sound.dfy"
    spec.write_text(SOUND)
    proc = _cli(["score", str(spec)], tmp_path, block_z3=True)
    assert proc.returncode == 2, proc.stdout
    assert scorer_mod.Z3_INSTALL_HINT in proc.stdout


def test_score_json_carries_the_state_for_machines(tmp_path):
    import json

    spec = tmp_path / "sound.dfy"
    spec.write_text(SOUND)
    proc = _cli(["score", str(spec), "--json"], tmp_path, block_z3=True,
                merge_stderr=False)
    payload = json.loads(proc.stdout)
    assert payload["vacuity_analysis"]["status"] == "could-not-analyse"
    assert payload["vacuity_analysis"]["remedy"] == "pip install z3-solver"
    assert payload["score_complete"] is False
