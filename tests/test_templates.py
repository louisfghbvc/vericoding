"""The prompt templates are the upstream source of spec quality.

These are not style checks. A prompt that tells the model to write
`assume false;` manufactures, at generation time, exactly the vacuity the
scorer and the verifier then have to catch -- and catching it downstream is
strictly worse than not producing it. So the instructions that matter are
asserted here, in the same suite as the code that compensates for them.
"""

import pathlib

TEMPLATES = pathlib.Path("templates")


def _read(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_spec_prompt_does_not_instruct_writing_assume_false():
    """The original text read:

        leave it as a method stub with `assume false;` or empty block for
        pure contract auditing

    `assume false;` makes the method unreachable, so every postcondition
    holds for any implementation. One sentence in a prompt, and every spec
    generated from it verifies while guaranteeing nothing.
    """
    text = _read("spec_prompt.md")
    assert "stub with `assume false;`" not in text
    assert "leave the body **empty**" in text


def test_spec_prompt_warns_about_vacuity():
    """A spec author needs to be told that an unreachable clause is worthless.

    No verifier flag reports this shape -- not --analyze-proofs, not
    --warn-contradictory-assumptions -- so if the prompt does not say it,
    nothing downstream will.
    """
    text = _read("spec_prompt.md").lower()
    assert "vacuity" in text
    assert "never write `requires false`" in text
    assert "==> true" in text


def test_impl_prompt_forbids_weakening_the_contract():
    """From FAILED to PASSED, the cheapest path is to weaken the `ensures`.

    The repair prompt hands the model the contract, the code, and the
    diagnostics, and asks for a green result. Unless it is told otherwise,
    editing the promise is a legitimate and cheap way to get one.
    """
    text = _read("impl_prompt.md")
    assert "Do not modify the contract" in text
    assert "assume false" in text
    assert "Never add a precondition to dodge a failing case" in text


def test_impl_prompt_goal_is_correctness_not_a_green_check():
    text = _read("impl_prompt.md")
    assert "correct with respect to its contract" in text


def test_review_checklist_asks_about_vacuity():
    """The human gate is the only place Shape-B vacuity can be caught by
    judgement rather than by a parser, so the checklist has to ask."""
    text = _read("review_checklist.md")
    assert "Vacuity" in text
    assert "NOT ANALYSED" in text
