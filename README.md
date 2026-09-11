# Vericoding: The End of "Trust Me Bro, The AI Wrote It"

> **Math, Not Vibes.** Formally verified AI code synthesis using Dafny and SMT Solvers (Z3).

Based on the methodology pioneered by ICME Labs:
Instead of blindly trusting LLM-generated code ("vibe coding"), **Vericoding** translates natural language intent into rigorous mathematical specifications, audits specifications for gaps using SMT solvers before writing code, proves implementations correct with 0 errors via DafnyPro repair loops, and archives portable cryptographic proof receipts (`receipt.json` + `.smt2`).

---

## The 7-Stage Pipeline

```
1. Natural Language Intent  ==> Precise user requirements
2. Formal Spec Synthesis   ==> Multi-pass LLM to Dafny contracts
3. SMT Spec Scoring        ==> Z3 consistency & gap analysis (before code is written)
4. Human Battle Testing    ==> Human reviews spec confidence & boundaries
5. DafnyPro Verified Impl  ==> Automated repair loop with Z3 diagnostics (Max 5x)
6. Target Language Compile ==> Dafny compiled to Python, Go, JavaScript, or C#
7. Cryptographic Receipt   ==> Portable SMT-LIB2 + SHA-256 verifiable seal
```

---

## Quick Start

### 1. Install Dependencies & Register Skill
```bash
brew install dafny
pip install z3-solver pytest
./install.sh
```

### 2. End-to-End Pipeline
Run the complete pipeline on any Dafny contract:
```bash
./bin/vericoding pipeline examples/bank_account/bank.dfy --target py --intent "Bank withdrawal with daily limit protection"
```

### 3. Step-by-Step Sub-workflows

| Command | Purpose |
| :--- | :--- |
| `vericoding score <file.dfy>` | Check vacuity, consistency & generate Spec Confidence % + Gap Report |
| `vericoding verify <file.dfy>` | Run Dafny & Z3 formal verification with structured diagnostic hints |
| `vericoding compile <file.dfy> --target [py\|go\|js]` | Compile verified Dafny code to native target languages |
| `vericoding receipt <file.dfy> --intent "..."` | Generate SMT-LIB2 proof artifact + cryptographic JSON receipt |
| `vericoding audit <receipt.json>` | Replay and verify SHA-256 hashes and proof validity of an archived receipt |

---

## Repository Structure

```
├── SKILL.md                 # Core AI Skill definition (Antigravity & Claude Code)
├── bin/vericoding           # Unified CLI executable
├── scripts/
│   ├── check_env.py         # Toolchain diagnostic checker
│   ├── spec_scorer.py       # SMT consistency & gap analyzer
│   ├── verify_loop.py       # DafnyPro diagnostic feedback loop
│   ├── compiler.py          # Multi-language compiler driver
│   └── receipt_generator.py # SMT-LIB2 and cryptographic receipt generator
├── templates/               # Prompt templates (spec, impl, review checklist)
├── examples/                # Tested examples (bank_account, rate_limiter)
└── tests/                   # Automated pytest suite
```
