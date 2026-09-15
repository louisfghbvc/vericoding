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


CLAUSE_KEYWORDS = ("requires", "ensures", "modifies", "reads", "decreases", "invariant")

# One clause runs from its keyword up to the next clause keyword or the body,
# NOT up to the next newline. A newline boundary silently truncates any clause
# a human wrapped for readability -- `ensures y == x &&` is not a shorter
# version of the clause, it is a broken fragment that the vacuity analysis then
# tries to interpret.
_CLAUSE_RE = re.compile(
    r'\b(' + '|'.join(CLAUSE_KEYWORDS) + r')\b\s+(.*?)'
    r'(?=\b(?:' + '|'.join(CLAUSE_KEYWORDS) + r')\b|$)',
    re.DOTALL,
)


def strip_comments(text: str) -> str:
    """Remove Dafny comments while preserving everything else's position.

    This is not cosmetic. Without it the parser reads commented-out clauses as
    real ones, and the effect runs the wrong way: adding

        // ensures y > 0
        // ensures y != -1

    to a spec changed nothing about what it guarantees but moved its score from
    70% MODERATE to 85% HIGH. Commenting a clause out increased confidence.

    String and char literals are honoured so that a `//` inside one survives,
    and block comments nest, as they do in Dafny. Newlines inside removed
    regions are kept so that line-based reasoning elsewhere still lines up.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch in '"\'':
            quote, j = ch, i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j

        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = n if end == -1 else end          # keep the newline itself

        elif text.startswith('/*', i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith('/*', j):
                    depth += 1
                    j += 2
                elif text.startswith('*/', j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            out.append('\n' * text.count('\n', i, j))
            i = j

        else:
            out.append(ch)
            i += 1

    return ''.join(out)


def _wraps_whole(text: str) -> bool:
    """True when the leading `(` is closed by the trailing `)`.

    `(a) && (b)` is balanced and starts and ends with parens, but the first
    one closes in the middle -- stripping them yields `a) && (b`. Only a scan
    distinguishes that from `(a && b)`.
    """
    depth = 0
    for index, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return index == len(text) - 1
    return False


def _unwrap_parens(text: str) -> str:
    """Remove parentheses that wrap the whole expression, however many."""
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _wraps_whole(text):
        text = text[1:-1].strip()
    return text


class DafnySpecParser:
    """Extracts methods, requires, ensures, and modifies clauses from Dafny code."""

    def __init__(self, content: str):
        # Comments are removed once, up front, so nothing downstream has to
        # remember to do it.
        self.content = strip_comments(content)
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

            requires = []
            ensures = []
            modifies = []
            for c_match in _CLAUSE_RE.finditer(spec_text):
                c_type = c_match.group(1)
                # Collapse the wrapping a human added for readability; a clause
                # spanning three lines is one clause, not a truncated one.
                c_val = " ".join(c_match.group(2).split()).rstrip(";").strip()
                if not c_val:
                    continue
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
                # The body is captured because vacuity can live there too, not
                # only in the clauses -- see BODY_VACUITY_RE below.
                "body": self._extract_body(rest, spec_block_match),
            })
        return methods

    @staticmethod
    def _extract_body(rest: str, spec_block_match) -> str:
        """Return the method body, brace-balanced, or '' if there is none."""
        if not spec_block_match:
            return ""
        start = spec_block_match.start()
        depth, i = 0, start
        while i < len(rest):
            if rest[i] == "{":
                depth += 1
            elif rest[i] == "}":
                depth -= 1
                if depth == 0:
                    return rest[start + 1:i]
            i += 1
        return rest[start + 1:]


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

        # Body-level vacuity, checked first because it makes every clause
        # below meaningless no matter how well written they are. `assume
        # false;` renders the rest of the method unreachable, so every
        # postcondition verifies against any implementation -- and the scorer
        # used to miss it entirely, because it only ever read clauses. A spec
        # with `assume false;` and a deliberately wrong body scored 85% HIGH
        # and was told its preconditions were free of vacuity.
        #
        # This matters most here rather than in verify_loop: `score` is the
        # stage a human uses to decide whether to approve a specification.
        body_vacuity = self._check_body_vacuity(method)
        if body_vacuity:
            return {
                "method_name": method["name"],
                "score": 0,
                "strengths": [],
                "gaps": [{
                    "category": "Body Vacuity",
                    "status": "VACUOUS",
                    "message": body_vacuity,
                }],
                "raw_requires": method["requires"],
                "raw_ensures": method["ensures"],
                "live_ensures": [],
                "vacuous_clauses": list(method["ensures"]),
                "unanalysed_clauses": [],
            }

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

        # 2c. Anything the analyser could not read is reported, never credited,
        # and costs exactly what a vacuous clause costs.
        #
        # Not crediting it is not enough. Vacuous was -20 and unanalysed was 0,
        # so ANY failure to read a clause was worth 20 points more than reading
        # it and finding it hollow -- and the cheapest way to get there is for
        # the solver to fall over. Measured on a spec with one vacuous
        # postcondition: 15 with a working z3, 30 with a broken one. Breaking
        # the analyser improved the score.
        #
        # On the evidence available, an unanalysed clause is indistinguishable
        # from a vacuous one; that is what "not analysed" means. Scoring it as
        # the better of the two possibilities is a claim the check did not
        # earn. The gap list still distinguishes NOT ANALYSED from VACUOUS, so
        # a human reviewer sees which is which -- but the number that feeds the
        # gate does not improve because the tool went blind.
        for u in unanalysed_ensures:
            clause = u["clause"] if isinstance(u, dict) else u
            why = u.get("why", "") if isinstance(u, dict) else ""
            gaps.append({
                "category": "Coverage",
                "status": "NOT ANALYSED",
                "message": f"`{clause}` was not checked for vacuity" + (f" — {why}" if why else "")
            })
            score -= 20

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
            # The claim has to be no stronger than the check. When a clause was
            # outside the fragment the solver never saw it, so "the
            # preconditions are jointly satisfiable" is not what was
            # established -- only that the readable SUBSET is. If the excluded
            # clause is itself unsatisfiable (`requires Valid(s)` where Valid
            # is false everywhere) the unqualified claim is simply wrong, and
            # it is wrong in the flattering direction. The gap list said
            # NOT ANALYSED all along; the strengths line contradicted it in
            # the one place a reader skims.
            unparsed = smt_check.get("unparsed", [])
            if unparsed:
                checked = smt_check.get("parsed_count", 0)
                strengths.append(
                    "The {} expressible precondition(s) are jointly satisfiable; "
                    "{} not analysed, so Shape-A vacuity is not ruled out.".format(
                        checked, len(unparsed))
                )
                score += 2   # a partial check is worth less than a whole one
            else:
                strengths.append("Preconditions are jointly satisfiable (no Shape-A vacuity).")
                score += 5
            for expr in unparsed:
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
            low = name.lower()
            # Literals are translated here rather than deferred to the caller.
            # Deferring meant the caller's checks -- which only ever saw the
            # bare forms -- let `!true` through as unparsed, so `requires
            # !true` was reported NOT ANALYSED and scored 65 while the
            # identical `requires false` scored 0. A negation is not a reason
            # to stop understanding a literal.
            if low in self.TRIVIALLY_TRUE or low in self.TRIVIALLY_FALSE:
                value = low in self.TRIVIALLY_TRUE
                return z3.BoolVal(not value if negated else value)
            var = bool_map.setdefault(name, z3.Bool(name))
            return z3.Not(var) if negated else var

        return None

    def _conjunction(self, text: str, var_map: Dict[str, Any],
                     bool_map: Dict[str, Any]):
        """Translate `a && b && c`, returning (terms, unparsed_parts)."""
        # Strip wrapping parentheses so `(a && b)` parses.
        #
        # Counting parens cannot do this. For `(x > 0) && (x < 5)` the counts
        # match, the string starts with "(" and ends with ")", and the inner
        # slice `x > 0) && (x < 5` ALSO has matching counts -- so the old
        # check passed twice over and handed the splitter mangled text. Both
        # conjuncts then failed to parse and an entirely idiomatic Dafny
        # precondition came back NOT ANALYSED, taking the Shape-A check with
        # it. The question is not "are the parens balanced" but "does the
        # FIRST one close at the very end", which needs a scan.
        text = _unwrap_parens(text)

        # And again per conjunct: declining to strip `(a) && (b)` as a whole is
        # correct, but it leaves each conjunct wearing its own parens, which
        # `_atom` cannot read either. Unwrapping only the outside fixed the
        # mangling and left the clause just as unanalysed.
        parts = [_unwrap_parens(p) for p in re.split(r'&&', text) if p.strip()]
        terms, unparsed = [], []
        for part in parts:
            # Literals are `_atom`'s job now. Keeping a second copy of the
            # rule here is what let `!true` slip between them: this loop
            # matched only the bare spellings and `_atom` deferred to this
            # loop, so the negated form belonged to neither. One place
            # understands literals.
            atom = self._atom(part, var_map, bool_map)
            if atom is None:
                unparsed.append(part)
            else:
                terms.append(atom)
        return terms, unparsed

    # `assume false` is the body-level equivalent of `requires false`: it makes
    # everything after it unreachable, so every postcondition holds for any
    # implementation. `assert false` reaches the same state by a different
    # route. Both are legitimate mid-proof tools in narrow cases, which is
    # exactly why they need to be surfaced rather than silently tolerated in a
    # specification a human is about to approve.
    BODY_VACUITY_RE = re.compile(r'\b(assume|assert)\s+false\s*;')

    def _check_body_vacuity(self, method: Dict[str, Any]):
        """Return a message if the body makes the contract unfalsifiable."""
        match = self.BODY_VACUITY_RE.search(method.get("body", "") or "")
        if not match:
            return None
        return (
            "`{}` in the body makes everything after it unreachable, so every "
            "postcondition above holds for ANY implementation -- including a "
            "wrong one. The proof will succeed and guarantee nothing. If this "
            "is a contract stub, leave the body empty instead."
        ).format(match.group(0))

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
            # parsed_count is how many assertions the solver actually reasoned
            # over. The caller needs it to state the sat result at the strength
            # the check earned, rather than generalising it to clauses the
            # solver never saw.
            return {"analysed": True, "has_contradiction": False,
                    "unparsed": unparsed_total,
                    "parsed_count": len(solver.assertions())}
        except Exception as ex:
            return {"analysed": False, "note": str(ex)}

    def _check_ensures_vacuity(self, method: Dict[str, Any]) -> Dict[str, Any]:
        """Shape B: can each postcondition's antecedent ever hold?"""
        result = {"vacuous": [], "unanalysed": [], "live": []}

        for ens in method["ensures"]:
            body = ens.strip()

            # The two blatant forms need no solver: `ensures true` and
            # `ensures X ==> true` are vacuous by reading, not by proving.
            # _check_smt_vacuity already catches `requires false` ahead of the
            # solver for exactly this reason, and leaving the mirror shapes
            # behind the z3 guard made the report's verdict depend on whether
            # a package happened to be installed -- on a host without z3 the
            # textbook Shape-B spec came back "not checked" rather than
            # "vacuous", which is honest but weaker than the evidence allowed.
            if body.lower() in self.TRIVIALLY_TRUE:
                result["vacuous"].append({
                    "clause": ens,
                    "why": "the postcondition is literally `true` and constrains nothing",
                })
                continue

            implies = self.RE_IMPLIES.match(body)
            if implies and implies.group(2).strip().lower() in self.TRIVIALLY_TRUE:
                result["vacuous"].append({
                    "clause": ens,
                    "why": "the consequent is literally `true`, so the implication "
                           "holds for every input regardless of the antecedent",
                })
                continue

            if not implies:
                # Not an implication, so there is no antecedent to be vacuous.
                # Also decidable by reading.
                result["live"].append(ens)
                continue

            if not z3:
                # Only the remaining question -- can this antecedent ever hold
                # under the preconditions? -- needs a solver. Unanalysed, not
                # live: a clause nobody checked must not be counted as one
                # that can fire.
                result["unanalysed"].append(ens)
                continue

            antecedent, consequent = implies.group(1).strip(), implies.group(2).strip()

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
