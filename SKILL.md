---
name: vericoding
description: Formally verify AI-generated code using SMT solvers and Dafny, moving from vibe coding to mathematical proof ("Math, Not Vibes"). Use whenever generating critical business logic, financial operations, smart contracts, rate limiters, or state machines requiring zero bugs, formal contract guarantees, and cryptographic proof receipts.
---

# Vericoding: The End of "Trust Me Bro, The AI Wrote It"

Vericoding replaces probabilistic guessing ("vibe coding") with mathematical proof. Rather than trusting that AI-generated code behaves correctly across edge cases, Vericoding compiles human intent into formal specifications (`requires`, `ensures`, `invariants`), audits the specification using SMT solvers (Z3) before writing code, proves the implementation correct via automated verification loops, and archives portable cryptographic proof receipts.

---

## The 7-Stage Vericoding Pipeline

```
1. Natural Language Intent  --> English requirements & boundary conditions
2. Formal Spec Synthesis   --> Multi-pass translation into Dafny contracts
3. SMT Spec Scoring        --> Z3 audits spec consistency & gap analysis
4. Human Battle Testing    --> Developer reviews high-level spec & gaps (not 500 lines of code)
5. LLM -> Verified Impl    --> DafnyPro repair loop (iterates until 0 verification errors)
6. Target Language Compile --> Compiles verified code into Python, Go, JS, or C#
7. Proof Receipt Archival  --> Generates portable SMT-LIB2 + SHA-256 audit receipt
```

---

## Sub-Workflows & Step-by-Step Commands

When executing a Vericoding task, the agent and developer proceed through the following sub-workflows:

### 1. Spec Synthesis (`/vericoding spec`)
Translate user intent into a formal Dafny specification stub with defensive preconditions and comprehensive postconditions.
- **Rules**:
  - Always enforce state preservation on failure: `ensures !success ==> state == old(state)`.
  - Always specify biconditional conditions: `ensures success <==> (condition)`.
  - Keep data models and invariants strictly typed.
- Reference template: `templates/spec_prompt.md`.

### 2. SMT Spec Scoring & Gap Analysis (`/vericoding score <file.dfy>`)
Audit the specification before any implementation code is generated.
```bash
./bin/vericoding score path/to/spec.dfy
```
- **Checks Performed**:
  - **Vacuity Detection**: Checks if preconditions are contradictory (which would make the spec vacuously true).
  - **Precondition & Postcondition Coverage**: Flags underspecified failure paths or unrestricted bounds.
  - **Confidence Score**: Outputs a percentage score (0-100%) and gap report (`✓ Fully Specified`, `⚠ Underspecified`, `✗ Unaddressed`).

### 3. Human Battle-Testing (`/vericoding review <file.dfy>`)
Present the Spec Confidence and Gap Checklist to the user.
- Ask the user to clarify domain decisions highlighted in the gap analysis (e.g. "What should happen if the limit is exceeded?", "Is zero allowed?").
- Reference checklist: `templates/review_checklist.md`.

### 4. DafnyPro Verified Implementation Loop (`/vericoding verify <file.dfy>`)
Implement the method body and invoke the formal verification engine.
```bash
./bin/vericoding verify path/to/spec.dfy
```
- If verification fails, parse the diagnostic error (unproved postcondition, invariant failure, non-termination).
- Iterate up to 5 times feeding precise solver counterexamples and line numbers back to repair the implementation.
- Reference repair guide: `templates/impl_prompt.md`.

### 5. Target Language Compilation (`/vericoding compile <file.dfy> --target [py|go|js]`)
Compile the mathematically verified Dafny code into your stack's target language:
```bash
./bin/vericoding compile path/to/spec.dfy --target py
```
The compiled output is completely free of runtime exceptions, null pointer bugs, or violated assertions.

### 6. Cryptographic Proof Receipt (`/vericoding receipt <file.dfy>`)
Produce standard-format proof artifacts and audit certificates:
```bash
./bin/vericoding receipt path/to/spec.dfy --intent "User requirements..."
```
- Generates `filename.receipt.json` containing:
  - SHA-256 digests of natural language intent, Dafny source, and SMT proof.
  - Verification transcript, solver version (Z3), and duration.
  - Tamper-evident receipt seal.
- To independently audit an existing receipt:
```bash
./bin/vericoding audit path/to/filename.receipt.json
```

---

## Diagnostic Quick-Fix Reference

| Dafny / Z3 Error | Root Cause | Vericoding Resolution |
| :--- | :--- | :--- |
| `a postcondition could not be proved` | Off-by-one or missing arithmetic fact | Add intermediate `assert <fact>;` or check edge conditions. |
| `cannot prove termination` | Missing variant for loop / recursion | Add `decreases <expression>` to the loop or method. |
| `loop invariant could not be proved` | Invariant doesn't hold at entry or after step | Split into base case & inductive step; assert bounds. |
| `precondition might not hold` | Calling code violates callee requirements | Enforce caller checks or strengthen caller precondition. |
| `unsatisfiable requires clauses` | Contradictory preconditions | Fix mutually exclusive `requires` detected by SMT solver. |

---

## Directory Reference

- `bin/vericoding`: Unified CLI orchestrator.
- `scripts/spec_scorer.py`: SMT consistency & gap analyzer.
- `scripts/verify_loop.py`: DafnyPro diagnostic feedback loop.
- `scripts/compiler.py`: Multi-language compiler driver.
- `scripts/receipt_generator.py`: Portable SMT-LIB2 & cryptographic JSON receipt generator.
- `examples/`: Battle-tested examples (`bank_account`, `rate_limiter`).
- `templates/`: Prompt engineering and review checklists.
