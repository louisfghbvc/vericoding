"""The function that turns Dafny's output into a verdict.

Every other guard in this file depends on `_classify` being right: the exit
codes, the report, and whatever a caller branches on. It reads the summary
line and never the exit code, because Dafny returns 4 for both a real proof
failure and a missing solver.

Coverage found eight of its lines unexercised, including the branch that
tells a reader the solver is what went wrong. A verdict function with
untested branches is the thing this repository exists to warn about.
"""

import pytest

from scripts.verify_loop import (
    DafnyVerifier,
    STATUS_FAILED,
    STATUS_PROVED,
    STATUS_TOOLCHAIN,
    STATUS_VACUOUS,
)

CLASSIFY = DafnyVerifier(dafny_path="/nonexistent/dafny")._classify

SUMMARY = "Dafny program verifier finished with {v} verified, {e} errors"


def test_clean_summary_is_proved():
    status, detail = CLASSIFY(SUMMARY.format(v=3, e=0))
    assert status == STATUS_PROVED
    assert "3 obligation(s) proved" in detail


def test_errors_present_is_a_proof_failure():
    status, detail = CLASSIFY(
        "x.dfy(4,0): Error: a postcondition could not be proved\n"
        + SUMMARY.format(v=1, e=2))
    assert status == STATUS_FAILED
    assert "2 error(s)" in detail


@pytest.mark.parametrize("warning", [
    "x.dfy(4,10): Warning: ensures clause proved using contradictory assumptions",
    "x.dfy(9,2): Warning: this assumption was not needed to complete the proof",
    "x.dfy(9,2): Warning: redundant assumption",
])
def test_a_clean_summary_with_a_vacuity_warning_is_not_a_proof(warning):
    """The summary line is identical to a real proof. That is the whole
    difficulty: `requires false` yields "1 verified, 0 errors" and exit 0."""
    status, detail = CLASSIFY(warning + "\n" + SUMMARY.format(v=1, e=0))
    assert status == STATUS_VACUOUS
    assert "holds for any implementation" in detail


def test_no_summary_at_all_is_a_toolchain_error():
    status, detail = CLASSIFY("something went wrong before verification began")
    assert status == STATUS_TOOLCHAIN
    assert "nothing was checked" in detail


def test_a_missing_solver_says_so_instead_of_blaming_the_source():
    """Dafny's own message for this is

        N resolution/type errors detected in <file>.dfy

    which points at the specification. The hint has to override that, or the
    reader edits a file that was never read."""
    status, detail = CLASSIFY(
        "CLI: Error: Z3 not found at /nonexistent/z3.\n"
        "1 resolution/type errors detected in x.dfy\n")
    assert status == STATUS_TOOLCHAIN
    assert "solver could not be located" in detail
    assert "VERICODING_Z3" in detail


def test_two_summaries_are_refused_rather_than_chosen_between():
    """Measured: Dafny emits exactly one summary even for several files in one
    invocation. So a second one means the transcript is not what this function
    was built to read.

    Picking a rule -- first match or last -- was the original behaviour, and
    the two readers of this line picked differently: this function took the
    first, tools/dafny-verify.sh took the last. They would have disagreed on
    precisely the input where it mattered. A second summary is more likely to
    have been forged than emitted, and neither rule is safe against that;
    refusing is.
    """
    status, detail = CLASSIFY(SUMMARY.format(v=1, e=0) + "\n" + SUMMARY.format(v=0, e=1))
    assert status == STATUS_TOOLCHAIN
    assert "2 verification summaries" in detail
    assert "establishes nothing" in detail
