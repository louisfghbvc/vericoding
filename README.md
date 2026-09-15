# Vericoding: The End of "Trust Me Bro, The AI Wrote It"

> **Math, Not Vibes.** Formally verified AI code synthesis using Dafny and SMT Solvers (Z3).

Based on the methodology pioneered by ICME Labs:
Instead of blindly trusting LLM-generated code ("vibe coding"), **Vericoding** translates natural language intent into rigorous mathematical specifications, audits specifications for gaps using SMT solvers before writing code, proves implementations correct with 0 errors via DafnyPro repair loops, and archives portable cryptographic proof receipts (`receipt.json` + `.smt2`).

---

## The 7-Stage Pipeline

```
1. Natural Language Intent  ==> Precise user requirements
2. Formal Spec Synthesis   ==> LLM writes Dafny contracts from the intent
3. SMT Spec Scoring        ==> Z3 vacuity & gap analysis, before code is written
4. Human Battle Testing    ==> The pipeline STOPS here until you pass --reviewed
5. DafnyPro Verified Impl  ==> Dafny+Z3 verdict with structured repair diagnostics
6. Target Language Compile ==> Dafny compiled to Python (see caveats below)
7. Cryptographic Receipt   ==> Portable SMT-LIB2, replayed and SHA-256 sealed
```

**Who does what.** Stages 1, 2 and 5's repair are done by an LLM driving this
CLI — `SKILL.md` is how an agent knows to do that. The CLI itself does 3, 4,
6, 7 and the verification half of 5. It does not call a model, and it does not
loop: `vericoding verify` runs Dafny once and returns diagnostics with repair
hints; whoever is driving decides whether to edit and re-run.

That division is the point of stage 4. Every automated check here answers
*does the implementation satisfy the spec*. None can answer *is this the right
spec*, and no solver ever will — a clause that says the wrong thing verifies
exactly as cleanly as one that says the right thing.

---

## Quick Start

### 1. Install Dependencies & Register Skill
```bash
# macOS
brew install dafny
# Linux: take the self-contained release, which bundles its own .NET
#   https://github.com/dafny-lang/dafny/releases

pip install z3-solver pytest
./install.sh          # symlinks only; --uninstall reverses it
```

Then confirm the host can actually do the work, rather than assuming:

```bash
./bin/vericoding env          # probes verification and each compile target
```

It exits non-zero if no proof can complete here. Dafny's *bundled* Z3 does not
run on every host — if the probe fails, point `VERICODING_Z3` at a working z3
(the one `pip install z3-solver` provides will do).

### 2. End-to-End Pipeline
```bash
./bin/vericoding pipeline examples/bank_account/bank.dfy \
    --target py --intent "Bank withdrawal with daily limit protection"
```

This stops at stage 4 and tells you to read the spec. Re-run with `--reviewed`
to record that you did and continue. Exit codes: `0` complete, `1` the spec
gate refused it, `3` waiting for review.

The spec gate refuses any specification containing a vacuous clause, and any
scoring below `--min-score` (default 60). `--force` overrides it and the
override is written into the receipt.

### 3. Step-by-Step Sub-workflows

| Command | Purpose |
| :--- | :--- |
| `vericoding score <file.dfy>` | Check vacuity, consistency & generate Spec Confidence % + Gap Report |
| `vericoding verify <file.dfy>` | Run Dafny & Z3 formal verification with structured diagnostic hints |
| `vericoding compile <file.dfy> --target [py\|go\|js\|cs]` | Compile verified Dafny to a target language — see the caveat below |
| `vericoding receipt <file.dfy> --intent "..."` | Generate SMT-LIB2 proof artifact + cryptographic JSON receipt |
| `vericoding audit <receipt.json>` | Re-check the hashes **and re-run the archived proof** through a solver |

**Compile targets.** Dafny verifies for all four, but only `py` finishes with
Dafny and Z3 alone. `go` additionally needs `goimports`, `js` needs Node plus
`bignumber.js`, and `cs` needs the .NET SDK. `vericoding env` probes each one
on your host and says which actually build.

**What `audit` establishes.** A matching hash says the file is the one that
was archived; it says nothing about whether the proof in it holds. `audit`
therefore re-runs the artifact through a solver and requires every obligation
to discharge. It exits `0` when every check passed, `1` when one failed, and
`2` when a check could not be run at all — because "verified" and "not checked"
are different facts and a tool about not overclaiming should not merge them.

---

## Repository Structure

```
├── SKILL.md                 # Core AI Skill definition (Antigravity & Claude Code)
├── pyproject.toml           # Python floor (3.8+) and pytest config
├── install.sh               # Symlink the skill into agent dirs; --uninstall
├── bin/vericoding           # Unified CLI executable
├── scripts/
│   ├── check_env.py         # Probes what this host can verify and compile
│   ├── spec_scorer.py       # Vacuity, consistency and gap analysis
│   ├── verify_loop.py       # Dafny verdict + structured repair diagnostics
│   ├── compiler.py          # Target-language compiler driver
│   └── receipt_generator.py # SMT-LIB2 emission, receipts, and replay
├── templates/               # Prompt templates (spec, impl, review checklist)
├── examples/                # Tested examples (bank_account, rate_limiter)
└── tests/                   # Automated pytest suite
```

---

## What this cannot tell you

Worth stating plainly in a project named after not overclaiming:

- **A proof is about the spec, not about your intent.** Stage 4 exists because
  nothing downstream of it can catch a specification that says the wrong thing.
- **Vacuity is not visible to the verifier.** A clause whose antecedent can
  never hold verifies in milliseconds and reports 0 errors. `vericoding score`
  is the only thing here that looks for that shape — and anything it cannot
  parse is reported as `NOT ANALYSED`, which is not a pass.
- **`NOT ANALYSED` and `could not be run` are not passes.** Several commands
  reserve a distinct exit code for them for exactly this reason.
