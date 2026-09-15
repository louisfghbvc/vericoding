#!/usr/bin/env python3
"""
check_env.py - Report what this environment can actually do.

The previous version inferred target-language readiness from whether a tool
was on PATH, and the inference was wrong for three of the four targets:

  * it checked `go`, but the blocker is `goimports` -- a separate binary that
    a Go installation does not necessarily provide
  * it checked `node`, but the JavaScript backend fails later, during module
    load
  * it collected `dotnet` and then never printed it, so the C# target was
    absent from the report entirely

It also treated `dafny --version` succeeding as "Ready". That is not
sufficient either: Dafny starts fine while its bundled Z3 fails to execute on
hosts whose glibc is older than it was built against, and the failure only
appears when a verification is attempted.

So this module probes. Every line it prints is the result of running the thing
it describes on a specification small enough that failing it can only be the
environment. It is slower than reading PATH, and it is the only answer worth
printing in a tool whose entire subject is not overstating what was checked.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Any, Optional

try:
    from compiler import TARGET_NAMES, TARGET_REQUIREMENTS
except ImportError:  # invoked as a module rather than as a script
    from scripts.compiler import TARGET_NAMES, TARGET_REQUIREMENTS


# Small enough that failing to verify or build it can only be the toolchain.
PROBE_SOURCE = """
method Probe(x: int) returns (y: int)
  ensures y == x
{ y := x; }
"""

VERIFY_TIMEOUT_S = 60
BUILD_TIMEOUT_S = 120


def check_tool(name: str, version_flag: str = "--version") -> Dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"installed": False, "path": None, "version": None}
    try:
        out = subprocess.run(
            [path, version_flag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        version_str = (out.stdout or out.stderr).strip().split("\n")[0]
        return {"installed": True, "path": path, "version": version_str}
    except Exception as e:
        return {"installed": True, "path": path, "version": f"error reading version: {e}"}


def check_python_z3() -> Dict[str, Any]:
    try:
        import z3
        return {"installed": True, "version": z3.get_version_string()}
    except ImportError:
        return {"installed": False, "version": None}


def _solver_args() -> list:
    """Honour an explicit solver, because the bundled one is not universal."""
    solver = os.environ.get("VERICODING_Z3")
    return [f"--solver-path={solver}"] if solver else []


def _run_probe(extra_args: list, timeout: int) -> Dict[str, Any]:
    """Run one Dafny invocation against the probe spec in a scratch dir."""
    dafny = shutil.which("dafny")
    if not dafny:
        return {"ok": False, "reason": "Dafny CLI not found on PATH"}

    workdir = tempfile.mkdtemp(prefix="vericoding-probe-")
    source = os.path.join(workdir, "probe.dfy")
    with open(source, "w") as handle:
        handle.write(PROBE_SOURCE)

    cmd = [dafny] + extra_args + _solver_args() + [source]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"timed out after {timeout}s"}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode == 0:
        return {"ok": True, "reason": None}

    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    first = next(
        (ln.strip() for ln in combined.splitlines()
         if ln.strip() and "verifier finished" not in ln),
        "exit code {}".format(proc.returncode),
    )
    return {"ok": False, "reason": first[:160]}


def probe_verification() -> Dict[str, Any]:
    """Can Dafny complete a verification here? The question `--version` dodges."""
    return _run_probe(["verify"], VERIFY_TIMEOUT_S)


def probe_target(target: str) -> Dict[str, Any]:
    """Can Dafny build for this target here?"""
    workdir = tempfile.mkdtemp(prefix="vericoding-build-")
    try:
        return _run_probe(
            ["build", f"--target:{target}", f"--output:{os.path.join(workdir, 'out')}"],
            BUILD_TIMEOUT_S,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_diagnostics(probe_targets: bool = True) -> Dict[str, Any]:
    tools = {
        "dafny": check_tool("dafny", "--version"),
        "z3_cli": check_tool("z3", "--version"),
        "z3_python": check_python_z3(),
        "dotnet": check_tool("dotnet", "--version"),
        "go": check_tool("go", "version"),
        "goimports": check_tool("goimports", "-h"),
        "node": check_tool("node", "--version"),
        "python3": check_tool("python3", "--version"),
    }
    tools["verification"] = probe_verification()
    tools["targets"] = (
        {t: probe_target(t) for t in sorted(TARGET_NAMES)} if probe_targets else {}
    )
    return tools


def print_report(probe_targets: bool = True) -> bool:
    """Print the report. Returns True if verification is possible here."""
    tools = run_diagnostics(probe_targets=probe_targets)

    print("========================================")
    print("   Vericoding Environment Diagnostics   ")
    print("========================================")

    dafny = tools["dafny"]
    if dafny["installed"]:
        print(f"[*] Dafny CLI       : found")
        print(f"    Path            : {dafny['path']}")
        print(f"    Version         : {dafny['version']}")
    else:
        print("[*] Dafny CLI       : ✗ not on PATH (install: https://github.com/dafny-lang/dafny/releases)")

    solver = os.environ.get("VERICODING_Z3")
    z3py, z3cli = tools["z3_python"], tools["z3_cli"]
    if solver:
        print(f"[*] Solver          : {solver} (from VERICODING_Z3)")
    elif z3cli["installed"]:
        print(f"[*] Solver          : {z3cli['path']} (from PATH)")
    else:
        print("[*] Solver          : Dafny's bundled Z3")
        print("      Note: the bundled binary does not run on every host. If the")
        print("      probe below fails, set VERICODING_Z3 to a working z3.")
    if z3py["installed"]:
        print(f"    Python z3       : {z3py['version']}")

    # The line that matters. Everything above describes what is installed;
    # this describes what works.
    verification = tools["verification"]
    if verification["ok"]:
        print("\n[*] Verification    : ✓ a proof completed successfully")
    else:
        print(f"\n[*] Verification    : ✗ NOT POSSIBLE -- {verification['reason']}")
        print("      Nothing downstream of this can be trusted until it is fixed.")

    if probe_targets:
        print("\nTarget languages (each probed by building a trivial program):")
        for target, result in tools["targets"].items():
            name = TARGET_NAMES.get(target, target)
            if result["ok"]:
                print(f"  ✓ {target:<3} {name:<11} builds")
            else:
                print(f"  ✗ {target:<3} {name:<11} {result['reason']}")
                requirement = TARGET_REQUIREMENTS.get(target)
                if requirement:
                    # "requires", not "needs": this states what the target
                    # depends on in general, and the probe's own error above
                    # says what actually failed here. Conflating the two
                    # produces confident wrong advice -- on this host Node.js
                    # was installed and the JavaScript build still failed.
                    print(f"      requires {requirement}")
        print("\n  Verification does not require any of these; only compilation does.")
    else:
        print("\nTarget languages    : not probed (--no-targets)")

    print("========================================")
    return bool(verification["ok"])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Report what this environment can do")
    parser.add_argument(
        "--no-targets", action="store_true",
        help="skip the per-target build probes (faster; verification is still probed)",
    )
    args = parser.parse_args()
    ok = print_report(probe_targets=not args.no_targets)
    # A non-zero exit lets CI and scripts branch on "can this host verify?"
    # without parsing the report.
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
