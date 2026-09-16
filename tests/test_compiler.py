"""Target-language selection.

Every alias in the documented list was dead. The dict held two kinds of value
at once -- display names and alias resolutions -- and the normalization built
on that confusion passed each alias straight through to Dafny, which rejects
all of them:

    $ dafny build --target:python tiny.dfy
    *** Error: No compiler found for target "python"; expecting one of 'cs' ...

So `--target python`, `golang`, `javascript` and `csharp` -- four of the eight
documented spellings -- could not work on any host.
"""

import pytest

from scripts.compiler import (
    DafnyCompiler,
    TARGET_ALIASES,
    TARGET_NAMES,
    TARGET_REQUIREMENTS,
)


@pytest.mark.parametrize("spelling,canonical", sorted(TARGET_ALIASES.items()))
def test_every_documented_spelling_resolves_to_a_real_dafny_target(spelling, canonical):
    assert canonical in TARGET_NAMES, (
        "{!r} resolves to {!r}, which is not a target Dafny accepts".format(
            spelling, canonical)
    )


def test_the_long_form_aliases_are_covered():
    """The four that were broken, named explicitly so a refactor cannot
    quietly drop them again."""
    assert TARGET_ALIASES["python"] == "py"
    assert TARGET_ALIASES["golang"] == "go"
    assert TARGET_ALIASES["javascript"] == "js"
    assert TARGET_ALIASES["csharp"] == "cs"


def test_every_target_declares_whether_it_needs_more_than_dafny():
    """Dafny verifies all four targets but can only finish the build for `py`
    without a further toolchain. Each one has to say which."""
    assert set(TARGET_REQUIREMENTS) == set(TARGET_NAMES)
    assert TARGET_REQUIREMENTS["py"] is None
    for target in ("go", "js", "cs"):
        assert TARGET_REQUIREMENTS[target], target


def test_unknown_target_is_rejected_with_the_valid_list(tmp_path):
    dfy = tmp_path / "t.dfy"
    dfy.write_text("method M() {}")
    res = DafnyCompiler(dafny_path="/nonexistent/dafny").compile(str(dfy), target="rust")
    assert res["success"] is False


def test_compiles_to_python_through_an_alias(tmp_path, toolchain):
    """End to end on the alias that used to fail outright."""
    dfy = tmp_path / "twice.dfy"
    dfy.write_text("""
    method Twice(x: int) returns (y: int)
      ensures y == 2 * x
    { y := 2 * x; }
    """)
    res = DafnyCompiler().compile(
        str(dfy), target="python", output_dir=str(tmp_path / "out")
    )
    assert res["success"] is True, res.get("raw_output")
    assert res["target"] == "py"


def test_alias_reaches_dafny_as_the_canonical_target(tmp_path, monkeypatch):
    """The defect, demonstrated at the only place it was observable.

    The dict lookup was always right -- SUPPORTED_TARGETS["python"] was "py"
    even before the fix. The bug lived in compile()'s normalization, which
    then handed Dafny the user's spelling instead. Nothing short of
    inspecting the command line shows that, which is why this test intercepts
    it rather than asserting on the mapping.
    """
    import subprocess as sp
    from scripts import compiler as compiler_mod

    seen = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(sp, "run", fake_run)
    monkeypatch.setattr(compiler_mod.subprocess, "run", fake_run, raising=False)

    dfy = tmp_path / "t.dfy"
    dfy.write_text("method M() {}")
    c = DafnyCompiler(dafny_path=__file__)  # any existing path; run is stubbed
    c.compile(str(dfy), target="python", output_dir=str(tmp_path / "o"))

    target_flags = [a for a in seen["cmd"] if a.startswith("--target")]
    assert target_flags == ["--target:py"], (
        "Dafny received {!r}; it rejects every spelling except the canonical "
        "one".format(target_flags)
    )
