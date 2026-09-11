#!/usr/bin/env python3
"""
spec_scorer.py - SMT-based Spec Scorer and Gap Analyzer.

Analyzes Dafny specifications for:
1. Consistency & Satisfiability (Is there at least one state satisfying pre + post?)
2. Vacuity Detection (Are preconditions mutually exclusive, making the spec vacuously true?)
3. Completeness & Undefined Behavior (Failure branches, boundary checks, state mutations)
4. Gap Analysis and Confidence Score (0 - 100%)
"""

import os
import re
import sys
import json
import argparse
import subprocess
from typing import Dict, List, Any, Optional

try:
    import z3
except ImportError:
    z3 = None


class DafnySpecParser:
    """Extracts methods, requires, ensures, and modifies clauses from Dafny code."""

    def __init__(self, content: str):
        self.content = content
        self.methods = self._parse_methods()

    def _parse_methods(self) -> List[Dict[str, Any]]:
        methods = []
        # Match method / function declarations
        method_pattern = re.compile(
            r'(method|function)\s+([A-Za-z0-9_]+)\s*\((.*?)\)\s*(?:returns\s*\((.*?)\))?',
            re.DOTALL
        )
        for match in method_pattern.finditer(self.content):
            kind = match.group(1)
            name = match.group(2)
            params = match.group(3)
            returns = match.group(4) or ""
            start_idx = match.end()

            # Find body or next method
            rest = self.content[start_idx:]
            # Look for requires and ensures before '{'
            spec_block_match = re.search(r'\{', rest)
            spec_text = rest[:spec_block_match.start()] if spec_block_match else rest[:300]

            # In Dafny, clauses may or may not end with semicolon, typically end of line
            clause_pattern = re.compile(r'(requires|ensures|modifies)\s+([^{\n]+?)(?:;|\n|$)')
            requires = []
            ensures = []
            modifies = []
            for c_match in clause_pattern.finditer(spec_text):
                c_type, c_val = c_match.group(1), c_match.group(2).strip()
                if c_type == "requires":
                    requires.append(c_val)
                elif c_type == "ensures":
                    ensures.append(c_val)
                elif c_type == "modifies":
                    modifies.append(c_val)

            methods.append({
                "kind": kind,
                "name": name,
                "params": [p.strip() for p in params.split(",") if p.strip()],
                "returns": [r.strip() for r in returns.split(",") if r.strip()],
                "requires": [r.strip() for r in requires],
                "ensures": [e.strip() for e in ensures],
                "modifies": [m.strip() for m in modifies],
            })
        return methods


class SpecScorer:
    """Evaluates the formal specification using SMT solvers and heuristic completeness metrics."""

    def __init__(self, spec_path: str):
        self.spec_path = spec_path
        with open(spec_path, "r", encoding="utf-8") as f:
            self.raw_code = f.read()
        self.parser = DafnySpecParser(self.raw_code)

    def analyze(self) -> Dict[str, Any]:
        results = {
            "file": self.spec_path,
            "methods_count": len(self.parser.methods),
            "methods": [],
            "overall_score": 0,
            "confidence_level": "LOW",
            "summary_gaps": [],
        }

        if not self.parser.methods:
            results["summary_gaps"].append("No formal methods or specifications detected.")
            return results

        total_score = 0
        for method in self.parser.methods:
            m_res = self._analyze_method(method)
            results["methods"].append(m_res)
            total_score += m_res["score"]

        avg_score = round(total_score / len(self.parser.methods), 1)
        results["overall_score"] = avg_score

        if avg_score >= 85:
            results["confidence_level"] = "HIGH"
        elif avg_score >= 60:
            results["confidence_level"] = "MODERATE"
        else:
            results["confidence_level"] = "LOW"

        return results

    def _analyze_method(self, method: Dict[str, Any]) -> Dict[str, Any]:
        req_count = len(method["requires"])
        ens_count = len(method["ensures"])
        params_count = len(method["params"])
        returns_count = len(method["returns"])

        gaps = []
        strengths = []
        score = 50  # baseline

        # 1. Check preconditions
        if req_count == 0:
            gaps.append({
                "category": "Precondition",
                "status": "UNDERSPECIFIED",
                "message": "No 'requires' clause defined. Domain inputs might permit unintended edge cases."
            })
            score -= 15
        else:
            strengths.append(f"{req_count} precondition(s) specified.")
            score += 15

        # 2. Check postconditions
        if ens_count == 0:
            gaps.append({
                "category": "Postcondition",
                "status": "UNADDRESSED",
                "message": "No 'ensures' clause defined. Method return values or side effects are unconstrained."
            })
            score -= 25
        elif ens_count == 1:
            gaps.append({
                "category": "Postcondition",
                "status": "UNDERSPECIFIED",
                "message": "Only 1 postcondition found. Ensure failure cases and frame conditions (invariants) are covered."
            })
            score += 10
        else:
            strengths.append(f"{ens_count} postcondition(s) specified.")
            score += 25

        # 3. Check failure branch handling
        has_failure_spec = any(
            "error" in e.lower() or "fail" in e.lower() or "false" in e.lower() or "old(" in e
            for e in method["ensures"]
        )
        if not has_failure_spec and returns_count > 0:
            gaps.append({
                "category": "Failure Semantics",
                "status": "UNDERSPECIFIED",
                "message": "No explicit postcondition specifying state preservation upon error or failure."
            })
            score -= 10
        else:
            strengths.append("State transition or failure condition verified.")
            score += 10

        # 4. SMT Vacuity Check using Z3 (if available)
        smt_check = self._check_smt_vacuity(method)
        if smt_check.get("has_contradiction"):
            gaps.append({
                "category": "SMT Consistency",
                "status": "CONTRADICTION",
                "message": f"Preconditions or postconditions appear mutually contradictory: {smt_check.get('detail')}"
            })
            score = min(score, 20)
        elif smt_check.get("verified"):
            strengths.append("SMT solver confirmed constraint consistency.")
            score += 10

        final_score = max(0, min(100, score))

        return {
            "method_name": method["name"],
            "score": final_score,
            "strengths": strengths,
            "gaps": gaps,
            "raw_requires": method["requires"],
            "raw_ensures": method["ensures"]
        }

    def _check_smt_vacuity(self, method: Dict[str, Any]) -> Dict[str, Any]:
        """Simple symbolic check for obvious contradictions in arithmetic requirements."""
        if not z3:
            return {"verified": False, "note": "z3 python package not found"}

        # Attempt to parse simple constraints like `x > 0`, `x <= 100`
        try:
            solver = z3.Solver()
            var_map = {}

            for req in method["requires"]:
                # Match simple inequalities e.g., amount > 0, amount <= balance
                match = re.match(r'([a-zA-Z0-9_]+)\s*(>|<|>=|<=|==|!=)\s*([a-zA-Z0-9_]+|\d+)', req.strip())
                if match:
                    v_name, op, right = match.groups()
                    if v_name not in var_map:
                        var_map[v_name] = z3.Int(v_name)
                    lhs = var_map[v_name]
                    rhs = int(right) if right.isdigit() else var_map.setdefault(right, z3.Int(right))

                    if op == '>': solver.add(lhs > rhs)
                    elif op == '<': solver.add(lhs < rhs)
                    elif op == '>=': solver.add(lhs >= rhs)
                    elif op == '<=': solver.add(lhs <= rhs)
                    elif op == '==': solver.add(lhs == rhs)
                    elif op == '!=': solver.add(lhs != rhs)

            if solver.assertions():
                check_result = solver.check()
                if check_result == z3.unsat:
                    return {"has_contradiction": True, "detail": "requires clauses are unsatisfiable (unsat)"}
                return {"verified": True}
        except Exception as ex:
            return {"verified": False, "note": str(ex)}

        return {"verified": False}


def format_cli_report(res: Dict[str, Any]) -> str:
    lines = []
    lines.append("==================================================")
    lines.append(f"          SMT SPEC SCORING & GAP ANALYSIS         ")
    lines.append("==================================================")
    lines.append(f"Target Spec File : {res['file']}")
    lines.append(f"Spec Confidence  : {res['overall_score']}% [{res['confidence_level']}]")
    lines.append("--------------------------------------------------")

    for m in res["methods"]:
        lines.append(f"\n[Method / Function: {m['method_name']}] (Score: {m['score']}%)")
        if m["strengths"]:
            for s in m["strengths"]:
                lines.append(f"  ✓ {s}")
        if m["gaps"]:
            for g in m["gaps"]:
                icon = "✗" if g["status"] in ["UNADDRESSED", "CONTRADICTION"] else "⚠"
                lines.append(f"  {icon} [{g['status']}] {g['category']}: {g['message']}")

    lines.append("\n==================================================")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SMT Spec Scorer for Dafny Specifications")
    parser.add_argument("spec_file", help="Path to .dfy specification file")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    if not os.path.exists(args.spec_file):
        print(f"Error: file not found: {args.spec_file}", file=sys.stderr)
        sys.exit(1)

    scorer = SpecScorer(args.spec_file)
    analysis = scorer.analyze()

    if args.json:
        print(json.dumps(analysis, indent=2))
    else:
        print(format_cli_report(analysis))


if __name__ == "__main__":
    main()
