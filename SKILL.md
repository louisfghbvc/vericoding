---
name: vericoding
description: Formally verify AI-generated code using SMT solvers and Dafny, moving from vibe coding to mathematical proof ("Math, Not Vibes"). Use whenever generating critical business logic, financial operations, smart contracts, rate limiters, or state machines requiring formal contract guarantees and replayable proof receipts.
---

# Vericoding: The End of "Trust Me Bro, The AI Wrote It"

Vericoding replaces probabilistic guessing with a contract a solver checks:
compile human intent into formal specifications (`requires`, `ensures`,
`invariants`), audit the specification with Z3 **before** writing code, prove
the implementation satisfies it, and archive a proof artifact anyone can
re-run.

**What a passing proof means, precisely:** the implementation satisfies *the
specification you wrote*. It does not mean the specification says what you
meant, and no solver can tell you that — a clause that says the wrong thing
verifies exactly as cleanly as one that says the right thing. Stage 4 exists
for that question and nothing else can answer it.

---

## The 7-Stage Pipeline, and who does each stage

```
1. Natural Language Intent  --> YOU (agent): English requirements & boundaries
2. Formal Spec Synthesis    --> YOU (agent): write Dafny contracts
3. SMT Spec Scoring         --> CLI: vericoding score
4. Human Battle Testing     --> HUMAN: the pipeline stops here until --reviewed
5. Verified Implementation  --> YOU (agent) write; CLI: vericoding verify judges
6. Target Compile           --> CLI: vericoding compile
7. Proof Receipt            --> CLI: vericoding receipt / audit
```

Stages 1, 2 and the repair half of 5 are **yours**. There is no `vericoding
spec` command and no `vericoding review` command — those are things you do,
not commands you run. The CLI's whole subcommand list is:

```
env  score  verify  compile  receipt  audit  pipeline
```

`vericoding verify` runs Dafny **once** and returns structured diagnostics
with repair hints. There is no built-in retry. You read the diagnostic, edit
the body, and run it again — that loop is yours to drive, and how many times
is your judgement.

---

## Before anything else

```bash
./bin/vericoding env
```

Probes whether a proof can actually complete here, and which compile targets
build. Exits non-zero if verification is impossible — **do not proceed past a
failure here**, because everything downstream would be reporting on a proof
that never ran. Dafny's bundled Z3 does not run on every host; if the probe
fails, set `VERICODING_Z3` to a working z3.

---

## Stage 2 — Writing the specification

Reference: `templates/spec_prompt.md`.

- Enforce state preservation on failure: `ensures !success ==> state == old(state)`.
- Specify success biconditionally: `ensures success <==> (condition)`.
- **Every clause must be able to fire.** Before writing one, ask whether its
  antecedent can ever hold. Under `requires n >= 0`, the clause
  `ensures n < 0 ==> !ok` is unreachable: it verifies instantly and protects
  nothing.
- **Never** write `ensures ... ==> true`, `ensures true`, `requires false`,
  `assume false;` or `assert false;`. Each makes the contract unfalsifiable,
  and the verifier will report success.
- More clauses is not better. One clause that can fire beats five that cannot.
- For a contract stub, leave the body **empty**. Not `assume false;`.

## Stage 3 — Score the specification

```bash
./bin/vericoding score path/to/spec.dfy
```

Three kinds of vacuity are detected, and they need different tests:

| shape | example | who else catches it |
| :--- | :--- | :--- |
| unsatisfiable preconditions | `requires false` | Dafny, under `--analyze-proofs` |
| unreachable antecedent | `ensures n < 0 ==> !ok` under `requires n >= 0` | **nothing else** |
| unfalsifiable body | `assume false;` | Dafny, under `--analyze-proofs` |

The middle row is why this stage exists. Nothing is contradictory there, so
Dafny proves the clause honestly and no flag reports anything.

Read the gap report, not the percentage. `✓` established, `⚠` weak, `✗`
defect, **`?` not analysed — which is not a pass.** Clauses the analyser could
not parse were neither credited nor cleared; read those yourself.

### What the analyser can read

The satisfiability check runs on a deliberately small expression fragment.
Knowing its edge tells you when a `?` means "your spec is unusual" rather
than "your spec is wrong":

| readable | example |
| :--- | :--- |
| integer comparison | `x > 0`, `x <= y` |
| boolean identifier, negated or not | `ok`, `!ok` |
| literals, negated or not | `true`, `false`, `!true` |
| conjunction, disjunction, parentheses | `(x > 0 \|\| y > 0) && x < 5` |
| implication | `ok ==> y == x` |
| biconditional (treated as having no antecedent) | `ok <==> x > 10` |

Everything else — quantifiers, uninterpreted predicates like `Valid()`,
arithmetic on both sides, sequence and set operators — is reported `?` and
**costs the clause the same as being vacuous**. That is deliberate: on the
evidence available, a clause nobody read is indistinguishable from one that
protects nothing, and scoring it as the better of the two is a claim the
check did not earn.

For the same reason a spec with any `?` is capped just below `HIGH`. If the
tool could not read all of it, "high confidence" is not available — however
good the rest looks.

One asymmetry worth knowing, because it decides which way the tool errs:
an unreadable **conjunct** is dropped (the check gets weaker, so a real
contradiction may be missed and is reported as `?`), while an unreadable
**disjunct** makes the whole clause `?` (dropping it would make the check
stronger and could accuse a sound spec of vacuity).

## Stage 4 — Human review

Present the gap report to the user. Ask about the domain decisions it
surfaces ("what should happen if the limit is exceeded?", "is zero allowed?").
Work through `templates/review_checklist.md`.

You cannot complete this stage on the user's behalf. `pipeline` stops here and
exits 3; `--reviewed` records that a human attested to reviewing, and the
receipt carries `human_review_attested` either way.

## Stage 5 — Implementation and verification

```bash
./bin/vericoding verify path/to/spec.dfy
```

Reference: `templates/impl_prompt.md`.

**Do not modify the contract to make verification pass.** From FAILED to
PROVED, the cheapest path is always to weaken an `ensures` or narrow a
`requires` until the counterexample falls outside the domain. Both make the
proof succeed by discarding the guarantee. If you believe a clause is wrong,
say so and stop — escalating is a valid outcome.

Exit codes, which are four because two is not enough:

| exit | meaning |
| :--- | :--- |
| 0 | `PROVED` |
| 1 | `PROOF_FAILED` — a real counterexample, read the diagnostic |
| 2 | `TOOLCHAIN_ERROR` — nothing was checked; fix the environment |
| 3 | `VACUOUS_PROOF` — verified on contradictory assumptions, guarantees nothing |

## Stage 6 — Compile

```bash
./bin/vericoding compile path/to/spec.dfy --target py
```

Targets: `py`, `go`, `js`, `cs` (long forms like `python` also work). Dafny
verifies for all four, but **only `py` completes with Dafny and Z3 alone** —
`go` needs `goimports`, `js` needs Node plus `bignumber.js`, `cs` needs the
.NET SDK. `vericoding env` says which build on this host.

**What compilation does and does not carry over.** The proof covers the Dafny
source against its contract. It does not cover the compiler: Dafny's backends
are not themselves verified, and the generated code still runs on a runtime
with its own failure modes (resource limits, FFI boundaries, the host). Saying
the output is "free of runtime exceptions" would be exactly the kind of claim
this project exists to stop making.

## Stage 7 — Receipt and audit

```bash
./bin/vericoding receipt path/to/spec.dfy --intent "User requirements..."
./bin/vericoding audit  path/to/spec.receipt.json
```

The receipt records SHA-256 digests of the intent, the source and the proof
artifact; the verification transcript; the spec-gate result including whether
it was overridden; and a tamper-evident seal over all of it.

`audit` recomputes the seal, re-hashes the source and the artifact, **and
re-runs the archived proof through a solver** — a matching hash only says the
file is the one that was archived, not that the proof in it holds. Exits 0
when every check passed, 1 when one failed, 2 when a check could not be run.

## End to end

```bash
./bin/vericoding pipeline spec.dfy --target py --intent "..." --reviewed
```

Runs 3 through 7. It refuses any specification containing a vacuous clause,
and any scoring below `--min-score` (default 60). `--force` overrides that and
writes the override into the receipt. Without `--reviewed` it stops at stage 4
and exits 3.

---

## Diagnostic Quick-Fix Reference

| Dafny / Z3 Error | Root Cause | Resolution |
| :--- | :--- | :--- |
| `a postcondition could not be proved` | Off-by-one or missing arithmetic fact | Add intermediate `assert <fact>;` — one you believe and Z3 can prove — or fix the edge case. |
| `cannot prove termination` | Missing variant for loop / recursion | Add `decreases <expression>`. |
| `loop invariant could not be proved` | Invariant fails at entry or after a step | Split into base case and inductive step; assert bounds. |
| `precondition might not hold` | Caller violates callee requirements | Fix the caller. Do not weaken the callee. |
| `Z3 not found` / no verification summary | Toolchain, not your code | Set `VERICODING_Z3`; run `vericoding env`. |
| `proved using contradictory assumptions` | `assume false` or unsatisfiable `requires` | The proof is vacuous. Remove the contradiction; do not silence the warning. |

---

## Directory Reference

- `bin/vericoding`: CLI orchestrator (`env score verify compile receipt audit pipeline`).
- `scripts/check_env.py`: probes what this host can verify and compile.
- `scripts/spec_scorer.py`: vacuity, consistency and gap analysis.
- `scripts/verify_loop.py`: Dafny verdict and repair diagnostics. One run, no loop.
- `scripts/compiler.py`: target-language compiler driver.
- `scripts/receipt_generator.py`: SMT-LIB2 emission, receipts, replay.
- `templates/`: spec prompt, implementation prompt, review checklist.
- `examples/`: worked examples (`bank_account`, `rate_limiter`).
- `tests/`: pytest suite.
