"""install.sh must not destroy anything.

It used to run `rm -rf` on ~/.claude/skills/vericoding and the Gemini
equivalent without looking at what was there. Measured in a sandbox HOME: a
real directory containing the user's own file was silently deleted and
replaced with a symlink. `set -e` offered no protection, because rm -rf
succeeded -- it did exactly what it was told.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
INSTALL = str(REPO / "install.sh")


def _run(home, args=()):
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", INSTALL, *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env, cwd=str(REPO), timeout=600,
    )


def test_refuses_to_replace_a_real_directory(tmp_path):
    """The defect, asserted on the artifact that used to disappear."""
    home = tmp_path / "home"
    skill_dir = home / ".claude" / "skills" / "vericoding"
    skill_dir.mkdir(parents=True)
    notes = skill_dir / "MY_NOTES.md"
    notes.write_text("something the user cared about")

    proc = _run(home)

    assert notes.exists(), "install.sh destroyed a pre-existing user directory"
    assert notes.read_text() == "something the user cared about"
    assert "REFUSING" in proc.stdout
    assert proc.returncode == 1


def test_replaces_its_own_symlink_without_complaint(tmp_path):
    """Re-running the installer is routine and must stay quiet."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    first = _run(home)
    assert first.returncode == 0, first.stdout
    link = home / ".claude" / "skills" / "vericoding"
    assert link.is_symlink()
    assert pathlib.Path(os.path.realpath(link)) == REPO

    second = _run(home)
    assert second.returncode == 0, second.stdout
    assert "REFUSING" not in second.stdout


def test_uninstall_removes_only_links_pointing_here(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    _run(home)

    # A link someone else put there, to somewhere else entirely.
    foreign_dir = tmp_path / "elsewhere"
    foreign_dir.mkdir()
    foreign = home / ".gemini" / "config" / "skills" / "vericoding"
    foreign.unlink()
    foreign.symlink_to(foreign_dir)

    proc = _run(home, ["--uninstall"])
    assert proc.returncode == 0, proc.stdout
    assert not (home / ".claude" / "skills" / "vericoding").exists()
    assert foreign.is_symlink(), "uninstall removed a link it did not create"


def test_install_succeeds_even_when_the_host_cannot_verify(tmp_path, monkeypatch):
    """A diagnostic that halts the thing it is diagnosing is worse than none.

    The install previously aborted partway through on any host that could not
    verify -- symlinks made, test suite never run, no summary either way.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = "/usr/bin:/bin"          # no dafny
    env.pop("VERICODING_Z3", None)
    proc = subprocess.run(
        ["bash", INSTALL], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env, cwd=str(REPO), timeout=600,
    )

    assert proc.returncode == 0, proc.stdout
    assert "Vericoding skill linked" in proc.stdout
    assert "cannot verify yet" in proc.stdout
    assert (home / ".claude" / "skills" / "vericoding").is_symlink()
