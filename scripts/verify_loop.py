#!/usr/bin/env python3
"""
verify_loop.py - DafnyPro Verification Runner & Error Diagnostic Parser.

Executes `dafny verify` and parses formal verification failures into
structured diagnostic objects suitable for iterative LLM self-repair.
"""

import io
import os
import re
import sys
import json
import shutil
import argparse
import subprocess
from typing import Dict, List, Any, Optional


# The summary line Dafny prints, which is the ONLY trustworthy source of a
# verdict. Neither the exit code nor the presence of the string "0 errors" is
# sufficient -- see STATUS_* below for why.
RE_SUMMARY = re.compile(
    r"Dafny program verifier finished with (\d+) verified, (\d+) error"
)

# Warnings that mean "this proof succeeded without establishing anything".
# Only emitted under --analyze-proofs, and crucially they do NOT change the
# summary line or the verified count.
RE_VACUITY = re.compile(
    r"Warning:.*(contradictory assumptions|was not needed to complete|redundant)",
    re.IGNORECASE,
)

# Four outcomes, because two is not enough to describe what Dafny can do.
#
#   PROVED     summary present, 0 errors, no vacuity warnings
#   FAILED     summary present, errors > 0 -- a real counterexample
#   VACUOUS    summary says verified, but the proof rests on contradictory or
#              unused assumptions. `requires false` lands here: the method is
#              unreachable so ANY body verifies, including a deliberately wrong
#              one. Dafny reports "1 verified, 0 errors" and exits 0.
#   TOOLCHAIN  no summary line at all -- solver missing, crashed, bad args.
#              Dafny returns exit code 4 for BOTH this and a real proof
#              failure, and on a missing solver it prints
#              "N resolution/type errors detected in <file>.dfy", blaming the
#              source for a toolchain problem. Collapsing this into FAILED
#              sends people debugging a spec that was never checked.
STATUS_PROVED = "PROVED"
STATUS_FAILED = "PROOF_FAILED"
STATUS_VACUOUS = "VACUOUS_PROOF"
STATUS_TOOLCHAIN = "TOOLCHAIN_ERROR"


class DafnyVerifier:
    """Invokes Dafny CLI to formally verify specifications and code."""

    def __init__(self, dafny_path: Optional[str] = None, solver_path: Optional[str] = None):
        self.dafny_bin = dafny_path or shutil.which("dafny")
        # Dafny's bundled Z3 is unusable on some hosts (e.g. it requires
        # GLIBC_2.34, which no LD_LIBRARY_PATH can supply on an older distro).
        # Allow an explicit solver, and honour VERICODING_Z3 so CI and
        # constrained hosts can point at a working one without code changes.
        self.solver_path = solver_path or os.environ.get("VERICODING_Z3")

    def is_available(self) -> bool:
        return bool(self.dafny_bin and os.path.exists(self.dafny_bin))

    def _build_cmd(self, file_path: str) -> List[str]:
        cmd = [self.dafny_bin, "verify", "--analyze-proofs", file_path]
        if self.solver_path:
            cmd.append(f"--solver-path={self.solver_path}")
        return cmd

    def verify(self, file_path: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "success": False,
                "status": STATUS_TOOLCHAIN,
                "verified": False,
                "error": "Dafny CLI not found on system PATH. Install with: brew install dafny",
                "diagnostics": [],
                "raw_output": "",
            }

        cmd = self._build_cmd(file_path)
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
            raw_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            status, detail = self._classify(raw_out)
            diagnostics = self._parse_diagnostics(raw_out, file_path)

            return {
                "success": status != STATUS_TOOLCHAIN,
                "status": status,
                # `verified` stays for backwards compatibility, but it is now
                # true ONLY for STATUS_PROVED. A vacuous proof is not a proof.
                "verified": status == STATUS_PROVED,
                "returncode": proc.returncode,
                "detail": detail,
                "vacuity_warnings": [
                    ln.strip() for ln in raw_out.splitlines() if RE_VACUITY.search(ln)
                ],
                "diagnostics": diagnostics,
                "raw_output": raw_out.strip(),
                "error": detail if status == STATUS_TOOLCHAIN else None,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "status": STATUS_TOOLCHAIN,
                "verified": False,
                "error": f"Verification timed out after {timeout_seconds}s (potential SMT solver loop or infinite proof search)",
                "diagnostics": [],
                "raw_output": "",
            }
        except Exception as ex:
            return {
                "success": False,
                "status": STATUS_TOOLCHAIN,
                "verified": False,
                "error": str(ex),
                "diagnostics": [],
                "raw_output": "",
            }

    def _classify(self, raw_out: str) -> tuple:
        """Derive the verdict from Dafny's output, never from its exit code."""
        summaries = RE_SUMMARY.findall(raw_out)

        # Measured: Dafny emits exactly one summary, even for several files in
        # one invocation -- it aggregates. So more than one means the output is
        # not what this function was built to read, and the honest answer is
        # that nothing was established, not a guess at which one counts.
        #
        # Guessing is what the two readers of this line used to do, and they
        # guessed differently: tools/dafny-verify.sh took the last match, this
        # took the first. They would disagree on exactly the input where it
        # mattered, and the reason to refuse rather than to pick a rule is that
        # a second summary is more likely to be forged than emitted.
        if len(summaries) > 1:
            return STATUS_TOOLCHAIN, (
                "Dafny's output contains {} verification summaries; exactly one "
                "is expected. Refusing to choose between them -- an ambiguous "
                "transcript establishes nothing.".format(len(summaries))
            )

        summary = RE_SUMMARY.search(raw_out)
        if not summary:
            hint = ""
            if "Z3 not found" in raw_out or "solver" in raw_out.lower():
                hint = (
                    " The solver could not be located. Pass --solver-path or set "
                    "VERICODING_Z3; Dafny's bundled Z3 does not run on every host."
                )
            return STATUS_TOOLCHAIN, (
                "Dafny produced no verification summary, so nothing was checked."
                + hint
            )

        verified_count, error_count = int(summary.group(1)), int(summary.group(2))
        if error_count > 0:
            return STATUS_FAILED, f"{verified_count} verified, {error_count} error(s)."

        vacuity = [ln.strip() for ln in raw_out.splitlines() if RE_VACUITY.search(ln)]
        if vacuity:
            return STATUS_VACUOUS, (
                f"{verified_count} verified with 0 errors, but {len(vacuity)} "
                "obligation(s) were proved from contradictory or unused "
                "assumptions. A proof that rests on an unsatisfiable premise "
                "holds for any implementation, including a wrong one."
            )

        return STATUS_PROVED, f"{verified_count} obligation(s) proved, 0 errors."

    def _parse_diagnostics(self, output: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Parses Dafny verification errors such as:
        test.dfy(14,12): Error: a postcondition could not be proved on this return path
        test.dfy(8,14): Related location: this is the postcondition that could not be proved
        """
        diagnostics = []
        pattern = re.compile(r'([^\s:]+)\((\d+),(\d+)\):\s*(Error|Warning|Related location):\s*(.+)')

        current_diag = None
        for line in output.splitlines():
            line = line.strip()
            match = pattern.match(line)
            if match:
                fpath, line_no, col_no, severity, message = match.groups()
                if severity == "Error":
                    current_diag = {
                        "file": fpath,
                        "line": int(line_no),
                        "column": int(col_no),
                        "severity": severity,
                        "message": message,
                        "related_locations": [],
                        "hint": self._generate_repair_hint(message),
                    }
                    diagnostics.append(current_diag)
                elif severity == "Related location" and current_diag:
                    current_diag["related_locations"].append({
                        "file": fpath,
                        "line": int(line_no),
                        "column": int(col_no),
                        "message": message,
                    })

        return diagnostics

    def _generate_repair_hint(self, message: str) -> str:
        msg_lower = message.lower()
        if "postcondition could not be proved" in msg_lower:
            return "Check return value calculation or add an intermediate `assert` / `lemma` to assist the SMT solver."
        elif "cannot prove termination" in msg_lower or "decreases" in msg_lower:
            return "Add an explicit `decreases <expression>` clause on the loop or recursive function."
        elif "invariant could not be proved" in msg_lower:
            return "Strengthen the loop `invariant` or check that variables are initialized correctly before entering the loop."
        elif "precondition might not hold" in msg_lower:
            return "Ensure the caller verifies the target function's `requires` clause prior to invocation."
        elif "index out of range" in msg_lower:
            return "Add a bounds check `0 <= idx < array.Length` or constraint."
        return "Review formal logical obligations and ensure assertions hold on all execution branches."


def format_cli_report(res: Dict[str, Any]) -> str:
    lines = []
    lines.append("==================================================")
    lines.append("       DAFNYPRO VERIFICATION STATUS REPORT        ")
    lines.append("==================================================")

    status = res.get("status", STATUS_TOOLCHAIN)

    if status == STATUS_TOOLCHAIN:
        lines.append("STATUS: [TOOLCHAIN ERROR] ⚙ Nothing was verified.")
        lines.append(f"  {res.get('error') or res.get('detail')}")
        lines.append("")
        lines.append("  This is neither a pass nor a failure. Treating it as either")
        lines.append("  would report on a proof that never ran.")
        lines.append("==================================================")
        return "\n".join(lines)

    if status == STATUS_PROVED:
        lines.append("STATUS: [PASSED] ✓ Formal verification succeeded!")
        lines.append(f"  {res.get('detail')}")
        lines.append("  No obligation relied on contradictory or unused assumptions.")

    elif status == STATUS_VACUOUS:
        lines.append("STATUS: [VACUOUS] ⚠ Verified, but the proof guarantees nothing.")
        lines.append(f"  {res.get('detail')}\n")
        for w in res.get("vacuity_warnings", []):
            lines.append(f"  | {w}")
        lines.append("")
        lines.append("  Most common cause: a `requires` clause that cannot be satisfied,")
        lines.append("  which makes the method unreachable so any body verifies.")
        lines.append("  Fix the precondition, do not weaken the postcondition.")

    else:
        lines.append("STATUS: [FAILED] ✗ SMT Solver found counterexamples or unproved goals.")
        lines.append(f"Total Errors Found: {len(res.get('diagnostics', []))}\n")

        for idx, d in enumerate(res.get("diagnostics", []), 1):
            lines.append(f"Error #{idx}: Line {d['line']}, Col {d['column']}")
            lines.append(f"  Reason : {d['message']}")
            if d.get("hint"):
                lines.append(f"  Hint   : 💡 {d['hint']}")
            for rel in d.get("related_locations", []):
                lines.append(f"  Related: {rel['file']}:{rel['line']} - {rel['message']}")
            lines.append("")

    lines.append("==================================================")
    return "\n".join(lines)


# Distinct exit codes so a caller (CI, the pipeline driver, a shell script) can
# branch on the outcome without re-parsing prose.
EXIT_CODES = {
    STATUS_PROVED: 0,
    STATUS_FAILED: 1,
    STATUS_TOOLCHAIN: 2,
    STATUS_VACUOUS: 3,
}


def _make_output_encoding_safe() -> None:
    """Stop the report from crashing on an ASCII-locale host.

    The report uses symbol characters, and a degraded host is exactly when the
    TOOLCHAIN_ERROR path fires -- a broken environment often also means
    LANG=C. Without this, the report that explains the breakage becomes a
    second, worse breakage: a UnicodeEncodeError traceback exiting 1, which a
    caller reads as a proof failure.

    `reconfigure` is Python 3.7+; the wrapper covers 3.6, which is still the
    system interpreter on several long-lived distributions.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        elif hasattr(stream, "buffer"):
            setattr(sys, name, io.TextIOWrapper(
                stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
            ))


def main():
    parser = argparse.ArgumentParser(description="DafnyPro Verification Runner & Diagnostic Tool")
    parser.add_argument("file_path", help="Path to .dfy file to verify")
    parser.add_argument("--json", action="store_true", help="Output diagnostics in JSON format")
    parser.add_argument("--timeout", type=int, default=30, help="Verification timeout in seconds")
    parser.add_argument(
        "--solver-path",
        default=None,
        help="Path to the z3 executable. Also read from VERICODING_Z3. "
             "Needed wherever Dafny's bundled solver cannot run.",
    )
    args = parser.parse_args()

    _make_output_encoding_safe()

    if not os.path.exists(args.file_path):
        print(f"Error: file not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    verifier = DafnyVerifier(solver_path=args.solver_path)
    res = verifier.verify(args.file_path, timeout_seconds=args.timeout)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(format_cli_report(res))

    sys.exit(EXIT_CODES.get(res.get("status"), 2))


if __name__ == "__main__":
    main()
