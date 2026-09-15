"""Diagnostic parsing, which is the repair loop's only input.

`vericoding verify` hands an agent a structured list of errors with line
numbers and hints, and the agent edits the body from that. If the parse is
wrong the agent repairs the wrong line, confidently -- and nothing downstream
would notice, because a wrong repair that still verifies looks exactly like a
right one.

Coverage measured 46% on verify_loop.py before these, with _parse_diagnostics
and _generate_repair_hint entirely unexercised.
"""

from scripts.verify_loop import DafnyVerifier

VERIFIER = DafnyVerifier(dafny_path="/nonexistent/dafny")

REAL_OUTPUT = """\
bank.dfy(14,12): Error: a postcondition could not be proved on this return path
bank.dfy(8,14): Related location: this is the postcondition that could not be proved
bank.dfy(31,4): Error: cannot prove termination; try supplying a decreases clause
Dafny program verifier finished with 2 verified, 2 errors
"""


def test_parses_line_and_column_from_a_real_transcript():
    diags = VERIFIER._parse_diagnostics(REAL_OUTPUT, "bank.dfy")
    assert len(diags) == 2
    first = diags[0]
    assert (first["line"], first["column"]) == (14, 12)
    assert "postcondition could not be proved" in first["message"]


def test_related_locations_attach_to_the_error_above_them():
    """A related location belongs to its error; floating free it points the
    agent at a line that is not the failure."""
    diags = VERIFIER._parse_diagnostics(REAL_OUTPUT, "bank.dfy")
    assert diags[0]["related_locations"], "related location was dropped"
    assert diags[0]["related_locations"][0]["line"] == 8
    # The second error has none, and must not inherit the first one's.
    assert diags[1]["related_locations"] == []


def test_warnings_are_not_reported_as_errors():
    """--analyze-proofs emits vacuity warnings in the same grammar. Counting
    them as errors would make a vacuous proof look like a failing one, which
    sends the agent editing code that verifies."""
    output = (
        "x.dfy(4,10): Warning: ensures clause proved using contradictory assumptions\n"
        "Dafny program verifier finished with 1 verified, 0 errors\n"
    )
    assert VERIFIER._parse_diagnostics(output, "x.dfy") == []


def test_no_diagnostics_from_a_clean_run():
    assert VERIFIER._parse_diagnostics(
        "Dafny program verifier finished with 3 verified, 0 errors\n", "x.dfy") == []


def test_every_hint_is_specific_to_its_error():
    """A hint that says the same thing for every error is decoration. Each
    branch has to name the construct that fixes that particular failure."""
    cases = {
        "a postcondition could not be proved on this return path": "assert",
        "cannot prove termination; try supplying a decreases clause": "decreases",
        "loop invariant could not be proved on entry": "invariant",
        "precondition might not hold": "requires",
        "index out of range": "bounds check",
    }
    hints = {}
    for message, expected in cases.items():
        hint = VERIFIER._generate_repair_hint(message)
        assert expected in hint, f"{message!r} -> {hint!r} does not mention {expected!r}"
        hints[message] = hint
    assert len(set(hints.values())) == len(cases), "two errors share a hint"


def test_an_unrecognised_error_gets_the_generic_hint_not_a_wrong_one():
    hint = VERIFIER._generate_repair_hint("something nobody has seen before")
    assert "Review formal logical obligations" in hint
