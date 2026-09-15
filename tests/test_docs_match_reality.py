"""The documentation is an interface, and SKILL.md is the agent-facing one.

An agent follows SKILL.md literally. When it named `/vericoding spec` and
`/vericoding review`, neither of which exists, following it meant running:

    vericoding: error: argument subcommand: invalid choice: 'review'
      (choose from env, score, verify, compile, receipt, audit, pipeline)

It also omitted `env` and `pipeline` -- the probe that says whether this host
can verify at all, and the command that runs the method end to end.

These tests treat every command and exit code the docs name as a claim to be
checked, for the same reason the rest of this repo checks its other claims.
"""

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CLI = str(REPO / "bin" / "vericoding")


def _subcommands():
    proc = subprocess.run(
        [sys.executable, CLI, "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60,
    )
    match = re.search(r"\{([a-z,]+)\}", proc.stdout)
    assert match, proc.stdout
    return set(match.group(1).split(","))


# Lines that mention a command in order to say it does NOT exist. Without
# this the check flags the very sentences added to prevent the bug.
_NEGATIONS = ("there is no", "no `vericoding", "does not provide", "invalid choice")


def _commands_the_doc_presents(doc):
    """Every command named in a code block OR an inline code span.

    Code blocks alone are not enough: the fake commands this test exists to
    catch -- `/vericoding spec` and `/vericoding review` -- appeared in
    section HEADINGS, in backticks, never in a runnable block. An agent reads
    headings.
    """
    text = (REPO / doc).read_text(encoding="utf-8")
    named = set()
    for line in text.splitlines():
        if any(neg in line.lower() for neg in _NEGATIONS):
            continue
        for m in re.finditer(r"`?(?:\./bin/|/)?vericoding\s+([a-z]+)", line):
            named.add(m.group(1))
    for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.DOTALL):
        for m in re.finditer(r"(?:\./bin/)?vericoding\s+([a-z]+)", block):
            named.add(m.group(1))
    # `description:` in the YAML frontmatter is not a subcommand.
    return named - {"description"}


def test_every_command_the_docs_present_exists():
    real = _subcommands()
    for doc in ("SKILL.md", "README.md"):
        named = _commands_the_doc_presents(doc)
        assert named, "{} presents no vericoding command at all".format(doc)
        unknown = {c for c in named if c not in real}
        assert not unknown, (
            "{} presents {} which the CLI does not provide (it has {})".format(
                doc, sorted(unknown), sorted(real))
        )


def test_skill_md_documents_every_command():
    """The reverse direction. `env` and `pipeline` were both missing, and they
    are the two an agent most needs: whether the host can verify at all, and
    how to run the method end to end."""
    text = (REPO / "SKILL.md").read_text(encoding="utf-8")
    for command in _subcommands():
        assert command in text, "SKILL.md never mentions `{}`".format(command)


def test_documented_verify_exit_codes_are_the_real_ones():
    """SKILL.md publishes a four-state table. If verify_loop's mapping drifts,
    an agent branching on those codes acts on the wrong verdict."""
    sys.path.insert(0, str(REPO))
    from scripts.verify_loop import (
        EXIT_CODES, STATUS_PROVED, STATUS_FAILED,
        STATUS_TOOLCHAIN, STATUS_VACUOUS,
    )

    assert EXIT_CODES[STATUS_PROVED] == 0
    assert EXIT_CODES[STATUS_FAILED] == 1
    assert EXIT_CODES[STATUS_TOOLCHAIN] == 2
    assert EXIT_CODES[STATUS_VACUOUS] == 3

    text = (REPO / "SKILL.md").read_text(encoding="utf-8")
    for status in ("PROVED", "PROOF_FAILED", "TOOLCHAIN_ERROR", "VACUOUS_PROOF"):
        assert status in text, "SKILL.md does not document {}".format(status)


def test_docs_do_not_promise_exception_free_output():
    """SKILL.md used to say, without qualification:

        The compiled output is completely free of runtime exceptions, null
        pointer bugs, or violated assertions.

    None of that follows from the proof. The proof covers the Dafny source
    against its contract; Dafny's compiler backends are not themselves
    verified, and the generated code still runs on a runtime with its own
    failure modes. In a document titled "The End of Trust Me Bro", an
    unqualified guarantee is the thing being argued against.
    """
    for doc in ("SKILL.md", "README.md"):
        text = (REPO / doc).read_text(encoding="utf-8").lower()
        assert "completely free of runtime exceptions" not in text
        assert "free of runtime exceptions, null pointer" not in text


def test_docs_do_not_claim_an_automated_repair_loop():
    """verify_loop.py calls the solver exactly once. Both docs claimed it
    iterated "up to 5 times", which no code anywhere implements."""
    source = (REPO / "scripts" / "verify_loop.py").read_text(encoding="utf-8")
    assert source.count("subprocess.run") == 1, (
        "a retry loop was added; the docs saying there is none are now wrong"
    )
    for doc in ("SKILL.md", "README.md"):
        text = (REPO / doc).read_text(encoding="utf-8")
        assert "Max 5x" not in text
        assert "up to 5 times" not in text


def test_every_expression_documented_as_readable_actually_parses(tmp_path):
    """SKILL.md's fragment table, executed.

    A documented capability nobody runs is a claim, and this repo's whole
    subject is claims that outrun their evidence. If the table drifts from
    the parser, a reader concludes their spec is unusual when it is simply
    unsupported -- or the reverse.

    The examples are read out of the document rather than restated here, so
    editing the table without editing the parser fails.
    """
    import pathlib
    import re

    from scripts.spec_scorer import SpecScorer

    skill = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"
    body = skill.read_text(encoding="utf-8")
    section = body.split("### What the analyser can read", 1)
    assert len(section) == 2, "the fragment section is gone from SKILL.md"
    table = section[1].split("Everything else", 1)[0]

    examples = []
    for row in table.splitlines():
        if not row.startswith("|") or ":---" in row or "example" in row:
            continue
        # Split on unescaped pipes only: a cell may contain `\|` (Dafny's `||`
        # has to be escaped inside a markdown table), and splitting there
        # truncates the expression into something that cannot parse -- making
        # the test fail on its own parsing rather than on the tool's.
        cells = [c.strip() for c in re.split(r'(?<!\\)\|', row.strip("|"))]
        if len(cells) < 2:
            continue
        examples += [e.strip(" `").replace("\\|", "|")
                     for e in cells[1].split("`, `") if e.strip(" `")]

    assert len(examples) >= 8, "parsed too few examples from the table: %r" % examples

    unreadable = []
    for expression in examples:
        if "==>" in expression:
            spec = ("method M(x: int, y: int) returns (ok: bool)\n"
                    "  requires x > 0\n  ensures %s\n{ ok := true; }\n" % expression)
            method = SpecScorer(_write(tmp_path, spec)).analyze()["methods"][0]
            if method["unanalysed_clauses"]:
                unreadable.append(expression)
            continue
        spec = ("method M(x: int, y: int, ok: bool) returns (r: int)\n"
                "  requires %s\n  ensures r == x\n{ r := x; }\n" % expression)
        gaps = SpecScorer(_write(tmp_path, spec)).analyze()["methods"][0]["gaps"]
        if any(g["status"] == "NOT ANALYSED" for g in gaps):
            unreadable.append(expression)

    assert not unreadable, (
        "SKILL.md lists these as readable but the analyser reports them "
        "unanalysed: %r" % unreadable)


def _write(tmp_path, spec):
    import hashlib
    path = tmp_path / ("doc_%s.dfy" % hashlib.sha256(spec.encode()).hexdigest()[:8])
    path.write_text(spec)
    return str(path)
