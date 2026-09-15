"""What the report says for each verdict, and what it must not say.

The four-state verdict is only worth having if the thing a human reads
distinguishes the four. A report that prints the success banner for a vacuous
proof has undone the work that produced the state, and it is the report --
not the status string -- that someone acts on.

So each test asserts the block that should appear AND the blocks that must
not. Coverage found all four rendering branches unexercised.
"""

import subprocess
import sys
import pathlib

import pytest

from scripts.verify_loop import (
    EXIT_CODES,
    STATUS_FAILED,
    STATUS_PROVED,
    STATUS_TOOLCHAIN,
    STATUS_VACUOUS,
    format_cli_report,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


def _result(status, **kw):
    base = {
        "status": status,
        "verified": status == STATUS_PROVED,
        "detail": "detail text",
        "diagnostics": [],
        "vacuity_warnings": [],
        "error": None,
    }
    base.update(kw)
    return base


def test_proved_shows_success_and_says_no_obligation_was_vacuous():
    out = format_cli_report(_result(STATUS_PROVED, detail="2 obligation(s) proved, 0 errors."))
    assert "[PASSED]" in out
    assert "contradictory or unused assumptions" in out
    assert "[VACUOUS]" not in out and "[FAILED]" not in out


def test_vacuous_never_shows_the_success_banner():
    """The whole point of the state. A vacuous proof reports "N verified,
    0 errors" from Dafny; if the report then says "succeeded" the reader has
    learned nothing the raw output did not already mislead them about."""
    out = format_cli_report(_result(
        STATUS_VACUOUS,
        detail="1 verified with 0 errors, but 2 obligation(s) ...",
        vacuity_warnings=["x.dfy(4,10): Warning: ensures clause proved using contradictory assumptions"],
    ))
    assert "[VACUOUS]" in out
    assert "guarantees nothing" in out
    assert "[PASSED]" not in out
    assert "succeeded" not in out.lower()
    # And it has to say what to do, because the obvious fix is the wrong one.
    assert "Fix the precondition, do not weaken the postcondition" in out


def test_toolchain_error_is_not_rendered_as_a_failure():
    """Rendering it as FAILED sends someone to debug a specification that was
    never read."""
    out = format_cli_report(_result(
        STATUS_TOOLCHAIN, error="Z3 not found at /nonexistent/z3"))
    assert "[TOOLCHAIN ERROR]" in out
    assert "Z3 not found" in out
    assert "neither a pass nor a failure" in out
    assert "[FAILED]" not in out and "[PASSED]" not in out


def test_failure_lists_each_diagnostic_with_its_location_and_hint():
    out = format_cli_report(_result(STATUS_FAILED, diagnostics=[{
        "file": "bank.dfy", "line": 14, "column": 12,
        "severity": "Error", "message": "a postcondition could not be proved",
        "related_locations": [{"file": "bank.dfy", "line": 8, "column": 4,
                               "message": "this is the postcondition"}],
        "hint": "Check return value calculation",
    }]))
    assert "[FAILED]" in out
    assert "Line 14, Col 12" in out
    assert "Check return value calculation" in out
    assert "bank.dfy:8" in out, "the related location is where the reader has to look"
    assert "[PASSED]" not in out


def test_every_status_maps_to_its_own_exit_code():
    """SKILL.md publishes this table and tells an agent to branch on it. Two
    states sharing a code would put the agent back where the exit-code work
    started."""
    assert EXIT_CODES == {
        STATUS_PROVED: 0,
        STATUS_FAILED: 1,
        STATUS_TOOLCHAIN: 2,
        STATUS_VACUOUS: 3,
    }
    assert len(set(EXIT_CODES.values())) == 4


@pytest.mark.parametrize("target,expect_in_output", [
    ("py", "Successfully compiled"),
    ("rust", "Unsupported target"),
])
def test_compiler_cli_reports_and_exits(tmp_path, target, expect_in_output):
    """The compiler's main() is what a user sees. An unsupported target has to
    list the ones that work rather than only rejecting."""
    spec = tmp_path / "x.dfy"
    spec.write_text("method M() {}")
    proc = subprocess.run(
        [sys.executable, "scripts/compiler.py", str(spec),
         "--target", target, "--out", str(tmp_path / "o")],
        cwd=str(REPO), capture_output=True, text=True, timeout=300)
    combined = proc.stdout + proc.stderr
    assert expect_in_output in combined, combined
    if target == "rust":
        assert proc.returncode == 1
        assert "py" in combined and "go" in combined, "rejection must list valid targets"
