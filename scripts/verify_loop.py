#!/usr/bin/env python3
"""
verify_loop.py - DafnyPro Verification Runner & Error Diagnostic Parser.

Executes `dafny verify` and parses formal verification failures into
structured diagnostic objects suitable for iterative LLM self-repair.
"""

import os
import re
import sys
import json
import shutil
import argparse
import subprocess
from typing import Dict, List, Any, Optional


class DafnyVerifier:
    """Invokes Dafny CLI to formally verify specifications and code."""

    def __init__(self, dafny_path: Optional[str] = None):
        self.dafny_bin = dafny_path or shutil.which("dafny")

    def is_available(self) -> bool:
        return bool(self.dafny_bin and os.path.exists(self.dafny_bin))

    def verify(self, file_path: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "success": False,
                "verified": False,
                "error": "Dafny CLI not found on system PATH. Install with: brew install dafny",
                "diagnostics": [],
                "raw_output": "",
            }

        cmd = [self.dafny_bin, "verify", file_path]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
            raw_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            is_verified = (
                proc.returncode == 0
                and "0 errors" in raw_out
                and ("verified, 0 errors" in raw_out or "Program has no errors" in raw_out)
            )

            diagnostics = self._parse_diagnostics(raw_out, file_path)

            return {
                "success": True,
                "verified": is_verified,
                "returncode": proc.returncode,
                "diagnostics": diagnostics,
                "raw_output": raw_out.strip(),
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "verified": False,
                "error": f"Verification timed out after {timeout_seconds}s (potential SMT solver loop or infinite proof search)",
                "diagnostics": [],
                "raw_output": "",
            }
        except Exception as ex:
            return {
                "success": False,
                "verified": False,
                "error": str(ex),
                "diagnostics": [],
                "raw_output": "",
            }

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

    if not res.get("success"):
        lines.append(f"Verification Execution Failed: {res.get('error')}")
        lines.append("==================================================")
        return "\n".join(lines)

    if res.get("verified"):
        lines.append("STATUS: [PASSED] ✓ Formal verification succeeded!")
        lines.append("All postconditions, preconditions, and invariants mathematically proven.")
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


def main():
    parser = argparse.ArgumentParser(description="DafnyPro Verification Runner & Diagnostic Tool")
    parser.add_argument("file_path", help="Path to .dfy file to verify")
    parser.add_argument("--json", action="store_true", help="Output diagnostics in JSON format")
    parser.add_argument("--timeout", type=int, default=30, help="Verification timeout in seconds")
    args = parser.parse_args()

    if not os.path.exists(args.file_path):
        print(f"Error: file not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    verifier = DafnyVerifier()
    res = verifier.verify(args.file_path, timeout_seconds=args.timeout)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(format_cli_report(res))

    if not res.get("verified"):
        sys.exit(1)


if __name__ == "__main__":
    main()
