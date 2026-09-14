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
        returns_count = len(method["returns"])

        gaps = []
        strengths = []
        score = 50  # baseline

        # Vacuity first, because it decides what the other counts MEAN. The
        # original scoring counted every `ensures` as evidence of rigour, which
        # inverts the incentive: adding a clause that can never fire raised the
        # score. Only live clauses may earn credit.
        smt_check = self._check_smt_vacuity(method)
        ens_check = self._check_ensures_vacuity(method)
        live_ensures = ens_check["live"]
        vacuous_ensures = ens_check["vacuous"]
        unanalysed_ensures = ens_check["unanalysed"]
        ens_count = len(live_ensures)

        # Shape A dominates everything else: if the preconditions cannot be
        # satisfied, every postcondition below is vacuous no matter how many
        # there are, and the implementation is unconstrained.
        if smt_check.get("has_contradiction"):
            gaps.append({
                # Kept as CONTRADICTION rather than VACUOUS: for Shape A the
                # contradiction is the cause and the vacuity is the effect, and
                # naming the cause tells the author what to edit. VACUOUS is
                # reserved for clause-level findings where there is no
                # contradiction to point at.
                "category": "SMT Consistency",
                "status": "CONTRADICTION",
                "message": f"Specification is vacuous: {smt_check.get('detail')}. "
                           "Every postcondition below holds trivially; this spec "
                           "verifies any implementation."
            })
            return {
                "method_name": method["name"],
                "score": 0,
                "strengths": [],
                "gaps": gaps,
                "raw_requires": method["requires"],
                "raw_ensures": method["ensures"],
                "vacuous_clauses": [e["clause"] for e in vacuous_ensures],
                "unanalysed_clauses": [
                    e["clause"] if isinstance(e, dict) else e for e in unanalysed_ensures
                ],
            }

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

        # 2. Check postconditions -- LIVE ones only.
        if ens_count == 0:
            gaps.append({
                "category": "Postcondition",
                "status": "UNADDRESSED",
                "message": (
                    "No postcondition that can ever fire. Method return values "
                    "or side effects are unconstrained."
                    + (f" ({len(vacuous_ensures)} clause(s) present but vacuous.)"
                       if vacuous_ensures else "")
                )
            })
            score -= 25
        elif ens_count == 1:
            gaps.append({
                "category": "Postcondition",
                "status": "UNDERSPECIFIED",
                "message": "Only 1 live postcondition found. Ensure failure cases and frame conditions (invariants) are covered."
            })
            score += 10
        else:
            strengths.append(f"{ens_count} live postcondition(s) specified.")
            score += 25

        # 2b. Vacuous postconditions are a defect, not neutral. Each one is a
        # clause a reader will count as protection that does not exist.
        for v in vacuous_ensures:
            gaps.append({
                "category": "Postcondition Vacuity",
                "status": "VACUOUS",
                "message": f"`{v['clause']}` — {v['why']}."
            })
            score -= 20

        # 2c. Anything the analyser could not read is reported, never credited.
        # Scoring an unread clause as sound is the same mistake this tool
        # exists to catch.
        for u in unanalysed_ensures:
            clause = u["clause"] if isinstance(u, dict) else u
            why = u.get("why", "") if isinstance(u, dict) else ""
            gaps.append({
                "category": "Coverage",
                "status": "NOT ANALYSED",
                "message": f"`{clause}` was not checked for vacuity" + (f" — {why}" if why else "")
            })

        # 3. Check failure branch handling. Only live clauses count -- a
        # vacuous clause mentioning "fail" is not failure coverage.
        has_failure_spec = any(
            "error" in e.lower() or "fail" in e.lower() or "old(" in e
            for e in live_ensures
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

        # 4. Report what the consistency check actually established. Note the
        # asymmetry: proving the preconditions SATISFIABLE is weak evidence
        # (it only rules out Shape A), so it earns little. The old scoring
        # awarded the same credit for "no contradiction found" as for "checked
        # and sound", which let an unparsed spec collect points for silence.
        if smt_check.get("analysed"):
            strengths.append("Preconditions are jointly satisfiable (no Shape-A vacuity).")
            score += 5
            for expr in smt_check.get("unparsed", []):
                gaps.append({
                    "category": "Coverage",
                    "status": "NOT ANALYSED",
                    "message": f"precondition `{expr}` was not expressible in the "
                               "supported fragment and did not constrain the check"
                })
        else:
            gaps.append({
                "category": "Coverage",
                "status": "NOT ANALYSED",
                "message": "Preconditions were not checked for satisfiability: "
                           + str(smt_check.get("note", "unknown reason"))
            })

        final_score = max(0, min(100, score))

        return {
            "method_name": method["name"],
            "score": final_score,
            "strengths": strengths,
            "gaps": gaps,
            "raw_requires": method["requires"],
            "raw_ensures": method["ensures"],
            "live_ensures": live_ensures,
            "vacuous_clauses": [v["clause"] for v in vacuous_ensures],
            "unanalysed_clauses": [
                u["clause"] if isinstance(u, dict) else u for u in unanalysed_ensures
            ],
        }

    # ------------------------------------------------------------------
    # Vacuity analysis
    # ------------------------------------------------------------------
    #
    # There are TWO shapes of vacuity and they need different tests. Missing
    # either one means scoring a specification that guarantees nothing.
    #
    # SHAPE A -- unsatisfiable preconditions. `requires false`, or a set of
    #   requires clauses with no common solution. The method becomes
    #   unreachable, so ANY body verifies -- including a deliberately wrong
    #   one. Dafny reports "1 verified, 0 errors" and exits 0; only
    #   --analyze-proofs warns, and even then the summary line is unchanged.
    #
    # SHAPE B -- a vacuously-true implication inside an `ensures`. Nothing is
    #   contradictory, so no Dafny flag says anything at all:
    #
    #       requires n >= 0
    #       ensures  n < 0 ==> !ok      <- antecedent can never hold
    #
    #   The implication is simply true and Dafny proves it honestly. This is
    #   the shape an LLM produces under a make-it-verify gradient, because it
    #   reads exactly like a real guard. Auditing the SPEC is the only way to
    #   see it.
    #
    # A note on what this analyser can and cannot do: it parses a deliberately
    # small fragment of Dafny expressions. Anything it cannot parse is
    # reported as NOT ANALYSED rather than silently treated as sound -- a
    # scorer that quietly assumes the clauses it failed to read are fine is
    # the same failure mode it exists to detect.

    OP_BUILDERS = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }
    RE_ATOM = re.compile(
        r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|==|!=|>|<)\s*(-?\d+|[a-zA-Z_][a-zA-Z0-9_]*)\s*$'
    )
    RE_IMPLIES = re.compile(r'^(.*?)==>(.*)$')
    TRIVIALLY_TRUE = {"true"}
    TRIVIALLY_FALSE = {"false"}

    # A bare identifier, optionally negated: `success`, `!success`. This is the
    # most common antecedent shape in real Dafny and skipping it left most
    # clauses unanalysed, which reads as "we checked nothing" even when the
    # spec is fine.
    RE_BOOL_ATOM = re.compile(r'^\s*(!?)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*$')

    def _atom(self, text: str, var_map: Dict[str, Any], bool_map: Dict[str, Any]):
        """Translate one comparison or boolean literal, or None if unparsed.

        Integer and boolean identifiers live in separate namespaces on purpose:
        the same name used as both would otherwise raise a Z3 sort error and
        take down the whole analysis, turning a parse limitation into a crash.
        """
        match = self.RE_ATOM.match(text)
        if match:
            lhs_name, op, rhs_text = match.groups()
            lhs = var_map.setdefault(lhs_name, z3.Int(lhs_name))
            try:
                rhs = z3.IntVal(int(rhs_text))
            except ValueError:
                rhs = var_map.setdefault(rhs_text, z3.Int(rhs_text))
            return self.OP_BUILDERS[op](lhs, rhs)

        bool_match = self.RE_BOOL_ATOM.match(text)
        if bool_match:
            negated, name = bool_match.groups()
            if name.lower() in self.TRIVIALLY_TRUE or name.lower() in self.TRIVIALLY_FALSE:
                return None  # handled by the caller's literal checks
            var = bool_map.setdefault(name, z3.Bool(name))
            return z3.Not(var) if negated else var

        return None

    def _conjunction(self, text: str, var_map: Dict[str, Any],
                     bool_map: Dict[str, Any]):
        """Translate `a && b && c`, returning (terms, unparsed_parts)."""
        # Strip one layer of wrapping parentheses so `(a && b)` parses.
        text = text.strip()
        while text.startswith("(") and text.endswith(")") and text.count("(") == text.count(")"):
            inner = text[1:-1].strip()
            if inner.count("(") != inner.count(")"):
                break
            text = inner

        parts = [p.strip() for p in re.split(r'&&', text) if p.strip()]
        terms, unparsed = [], []
        for part in parts:
            low = part.strip().lower()
            if low in self.TRIVIALLY_TRUE:
                continue
            if low in self.TRIVIALLY_FALSE:
                terms.append(z3.BoolVal(False))
                continue
            atom = self._atom(part, var_map, bool_map)
            if atom is None:
                unparsed.append(part)
            else:
                terms.append(atom)
        return terms, unparsed

    def _check_smt_vacuity(self, method: Dict[str, Any]) -> Dict[str, Any]:
        """Shape A: are the preconditions jointly satisfiable?"""
        if not z3:
            return {"analysed": False, "note": "z3 python package not found"}

        # `requires false` needs no solver and no parser.
        for req in method["requires"]:
            if req.strip().lower() in self.TRIVIALLY_FALSE:
                return {
                    "analysed": True,
                    "has_contradiction": True,
                    "detail": "`requires false` makes the method unreachable, so "
                              "every implementation verifies, including a wrong one",
                }

        try:
            var_map: Dict[str, Any] = {}
            bool_map: Dict[str, Any] = {}
            solver = z3.Solver()
            unparsed_total = []
            for req in method["requires"]:
                terms, unparsed = self._conjunction(req, var_map, bool_map)
                unparsed_total.extend(unparsed)
                for t in terms:
                    solver.add(t)

            if not solver.assertions():
                return {"analysed": False, "unparsed": unparsed_total,
                        "note": "no precondition was expressible in the supported fragment"}

            if solver.check() == z3.unsat:
                return {
                    "analysed": True,
                    "has_contradiction": True,
                    "detail": "the requires clauses have no common solution (unsat), "
                              "so the method is unreachable and any body verifies",
                }
            return {"analysed": True, "has_contradiction": False,
                    "unparsed": unparsed_total}
        except Exception as ex:
            return {"analysed": False, "note": str(ex)}

    def _check_ensures_vacuity(self, method: Dict[str, Any]) -> Dict[str, Any]:
        """Shape B: can each postcondition's antecedent ever hold?"""
        result = {"vacuous": [], "unanalysed": [], "live": []}
        if not z3:
            result["unanalysed"] = list(method["ensures"])
            return result

        for ens in method["ensures"]:
            body = ens.strip()

            if body.lower() in self.TRIVIALLY_TRUE:
                result["vacuous"].append({
                    "clause": ens,
                    "why": "the postcondition is literally `true` and constrains nothing",
                })
                continue

            implies = self.RE_IMPLIES.match(body)
            if not implies:
                # Not an implication, so there is no antecedent to be vacuous.
                result["live"].append(ens)
                continue

            antecedent, consequent = implies.group(1).strip(), implies.group(2).strip()

            if consequent.lower() in self.TRIVIALLY_TRUE:
                result["vacuous"].append({
                    "clause": ens,
                    "why": "the consequent is literally `true`, so the implication "
                           "holds for every input regardless of the antecedent",
                })
                continue

            try:
                var_map: Dict[str, Any] = {}
                bool_map: Dict[str, Any] = {}
                solver = z3.Solver()
                # The antecedent is only reachable under the preconditions, so
                # they are part of the question.
                pre_unparsed = []
                for req in method["requires"]:
                    terms, unparsed = self._conjunction(req, var_map, bool_map)
                    pre_unparsed.extend(unparsed)
                    for t in terms:
                        solver.add(t)

                ante_terms, ante_unparsed = self._conjunction(antecedent, var_map, bool_map)
                if ante_unparsed or not ante_terms:
                    result["unanalysed"].append({
                        "clause": ens,
                        "why": f"antecedent not expressible in the supported fragment: "
                               f"{', '.join(ante_unparsed) or antecedent}",
                    })
                    continue
                for t in ante_terms:
                    solver.add(t)

                if solver.check() == z3.unsat:
                    result["vacuous"].append({
                        "clause": ens,
                        "why": "the antecedent can never hold under the preconditions, "
                               "so this clause never fires and protects nothing",
                    })
                else:
                    result["live"].append(ens)
            except Exception as ex:
                result["unanalysed"].append({"clause": ens, "why": str(ex)})

        return result


def format_cli_report(res: Dict[str, Any]) -> str:
    lines = []
    lines.append("==================================================")
    lines.append(f"          SMT SPEC SCORING & GAP ANALYSIS         ")
    lines.append("==================================================")
    lines.append(f"Target Spec File : {res['file']}")
    lines.append(f"Spec Confidence  : {res['overall_score']}% [{res['confidence_level']}]")
    lines.append("--------------------------------------------------")

    ICONS = {
        "UNADDRESSED": "✗",
        "CONTRADICTION": "✗",
        "VACUOUS": "✗",
        "NOT ANALYSED": "?",
    }

    for m in res["methods"]:
        lines.append(f"\n[Method / Function: {m['method_name']}] (Score: {m['score']}%)")
        if m["strengths"]:
            for s in m["strengths"]:
                lines.append(f"  ✓ {s}")
        if m["gaps"]:
            for g in m["gaps"]:
                icon = ICONS.get(g["status"], "⚠")
                lines.append(f"  {icon} [{g['status']}] {g['category']}: {g['message']}")

    lines.append("")
    lines.append("Legend: ✓ established   ⚠ weak   ✗ defect   ? not analysed")
    lines.append("A '?' is not a pass. Clauses this tool could not read were")
    lines.append("neither credited nor cleared -- read them yourself.")
    lines.append("==================================================")
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
