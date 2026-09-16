"""What the environment report says when the probe itself cannot run.

`vericoding env` exists to answer one question -- can this host verify? -- and
it answers by running a verification rather than by reading PATH. So the
paths that matter most are the ones where the probe does not complete: a
missing Dafny, a timeout, an unexpected crash. Each has to come back as "not
possible" with the cause named, because the command's exit code is a gate that
CI and install.sh both branch on.

Coverage found those handlers unexercised, along with the branch that tells a
user their solver is Dafny's bundled Z3 -- which carries the warning that it
does not run on every host, and is therefore the single most useful line in
the report for the person most likely to need it.
"""

import subprocess

import pytest

from scripts import check_env


def test_probe_reports_a_missing_dafny_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(check_env.shutil, "which", lambda name: None)
    result = check_env._run_probe(["verify"], 10)
    assert result["ok"] is False
    assert "not found" in result["reason"].lower()


def test_probe_reports_a_timeout_with_the_budget(monkeypatch, tmp_path):
    fake = tmp_path / "dafny"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(check_env.shutil, "which", lambda name: str(fake))

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 45)

    monkeypatch.setattr(check_env.subprocess, "run", fake_run)
    result = check_env._run_probe(["verify"], 45)
    assert result["ok"] is False
    assert "45s" in result["reason"], "a timeout without its budget cannot be acted on"


def test_probe_reports_an_unexpected_crash(monkeypatch, tmp_path):
    fake = tmp_path / "dafny"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(check_env.shutil, "which", lambda name: str(fake))
    monkeypatch.setattr(
        check_env.subprocess, "run",
        lambda cmd, **kw: (_ for _ in ()).throw(OSError("Exec format error")))
    result = check_env._run_probe(["verify"], 10)
    assert result["ok"] is False
    assert "Exec format error" in result["reason"]


def test_report_says_verification_is_impossible_and_why(monkeypatch, capsys):
    monkeypatch.setattr(check_env, "probe_verification",
                        lambda: {"ok": False, "reason": "solver crashed"})
    ok = check_env.print_report(probe_targets=False)
    out = capsys.readouterr().out
    assert ok is False
    assert "NOT POSSIBLE" in out and "solver crashed" in out


def test_report_names_an_explicit_solver(monkeypatch, capsys):
    monkeypatch.setenv("VERICODING_Z3", "/opt/z3/bin/z3")
    monkeypatch.setattr(check_env, "probe_verification", lambda: {"ok": True, "reason": None})
    check_env.print_report(probe_targets=False)
    out = capsys.readouterr().out
    assert "/opt/z3/bin/z3" in out and "VERICODING_Z3" in out


def test_report_warns_when_falling_back_to_the_bundled_solver(monkeypatch, capsys):
    """The most useful line in the report for the person most likely to need
    it: Dafny's bundled Z3 does not run on every host, and when it does not,
    nothing else in the output explains why."""
    monkeypatch.delenv("VERICODING_Z3", raising=False)
    monkeypatch.setattr(check_env, "check_tool",
                        lambda name, flag="--version": {"installed": False, "path": None, "version": None})
    monkeypatch.setattr(check_env, "check_python_z3", lambda: {"installed": False, "version": None})
    monkeypatch.setattr(check_env, "probe_verification", lambda: {"ok": True, "reason": None})
    check_env.print_report(probe_targets=False)
    out = capsys.readouterr().out
    assert "bundled Z3" in out
    assert "does not run on every host" in out
    assert "VERICODING_Z3" in out


def test_report_points_at_the_release_page_when_dafny_is_absent(monkeypatch, capsys):
    monkeypatch.setattr(check_env, "check_tool",
                        lambda name, flag="--version": {"installed": False, "path": None, "version": None})
    monkeypatch.setattr(check_env, "check_python_z3", lambda: {"installed": False, "version": None})
    monkeypatch.setattr(check_env, "probe_verification",
                        lambda: {"ok": False, "reason": "Dafny CLI not found on PATH"})
    check_env.print_report(probe_targets=False)
    out = capsys.readouterr().out
    assert "not on PATH" in out
    assert "github.com/dafny-lang/dafny/releases" in out


def test_an_unreadable_version_does_not_claim_the_tool_is_missing(monkeypatch):
    """`installed` and `version` answer different questions. Conflating them
    would report a working Dafny as absent because `--version` misbehaved."""
    monkeypatch.setattr(check_env.shutil, "which", lambda name: "/usr/bin/thing")
    monkeypatch.setattr(
        check_env.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    result = check_env.check_tool("thing")
    assert result["installed"] is True
    assert "error reading version" in result["version"]


def test_absent_python_z3_is_reported_as_absent(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_z3(name, *args, **kwargs):
        if name == "z3":
            raise ImportError("no module named z3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_z3)
    assert check_env.check_python_z3() == {"installed": False, "version": None}
