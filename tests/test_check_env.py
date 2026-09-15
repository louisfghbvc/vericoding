"""The environment report.

The previous version inferred target readiness from whether a tool was on
PATH, and the inference was wrong for three of the four targets: it checked
`go` when the blocker is `goimports`, it checked `node` when the JavaScript
backend fails during module load, and it collected `dotnet` and never printed
it. It also called `dafny --version` succeeding "Ready", which it is not --
Dafny starts fine while its bundled Z3 cannot execute.

So the report probes. These tests cover the reporting logic; the probes
themselves are stubbed, because the point under test is what the report says
about a result, not whether this host happens to have Go.
"""

from scripts import check_env


def test_report_fails_when_verification_is_impossible(monkeypatch, capsys):
    monkeypatch.setattr(
        check_env, "probe_verification",
        lambda: {"ok": False, "reason": "Z3 not found at /nonexistent/z3"},
    )
    ok = check_env.print_report(probe_targets=False)
    out = capsys.readouterr().out
    assert ok is False
    assert "NOT POSSIBLE" in out
    assert "Z3 not found" in out
    # The consequence has to be spelled out; a red line the reader skims past
    # is the same as no line.
    assert "Nothing downstream of this can be trusted" in out


def test_report_succeeds_when_a_proof_completes(monkeypatch, capsys):
    monkeypatch.setattr(check_env, "probe_verification", lambda: {"ok": True, "reason": None})
    ok = check_env.print_report(probe_targets=False)
    assert ok is True
    assert "a proof completed successfully" in capsys.readouterr().out


def test_a_failing_target_reports_the_real_error_and_the_requirement(monkeypatch, capsys):
    """Both, and kept distinct.

    On the host this was measured, Node.js WAS installed and the JavaScript
    build still failed, on a missing `bignumber.js`. A report that printed
    only "needs Node.js" would have been confidently wrong, so the probe's own
    error is shown and the requirement is phrased as what the target depends
    on rather than as a diagnosis.
    """
    monkeypatch.setattr(check_env, "probe_verification", lambda: {"ok": True, "reason": None})
    monkeypatch.setattr(
        check_env, "probe_target",
        lambda t: {"ok": t == "py", "reason": None if t == "py" else "Cannot find module 'bignumber.js'"},
    )
    check_env.print_report(probe_targets=True)
    out = capsys.readouterr().out
    assert "Cannot find module 'bignumber.js'" in out
    assert "requires" in out
    assert "needs Node.js" not in out
    # Verification must not be confused with compilation.
    assert "Verification does not require any of these" in out


def test_diagnostics_probe_every_documented_target():
    """A target the compiler accepts but the report never mentions is how the
    C# target went missing from the old report entirely."""
    from scripts.compiler import TARGET_NAMES

    import scripts.check_env as ce
    captured = {}

    original = ce.probe_target
    try:
        ce.probe_target = lambda t: captured.setdefault(t, {"ok": True, "reason": None})
        ce.probe_verification = lambda: {"ok": True, "reason": None}
        ce.run_diagnostics(probe_targets=True)
    finally:
        ce.probe_target = original

    assert set(captured) == set(TARGET_NAMES)
