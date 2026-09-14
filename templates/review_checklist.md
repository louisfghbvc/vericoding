# Human Battle-Testing: Spec Review Checklist

Before letting an AI write or verify implementation code, review the formal contract to prevent "verified garbage" (code that correctly implements an incomplete or flawed specification).

## Checklist Questions

- [ ] **Precondition Completeness**:
  - Are invalid or negative inputs forbidden (e.g. negative withdrawals, null pointers, empty arrays)?
  - Are bounds and limits explicitly stated?
- [ ] **Failure Semantics**:
  - If an operation fails, does the specification explicitly enforce that state remains unchanged?
  - (`ensures !success ==> state == old(state)`)
- [ ] **Exclusivity & Determinism**:
  - Is the condition for success mutually exclusive and exhaustive with the condition for failure?
  - (`ensures success <==> condition`)
- [ ] **Frame & Invariant Preservation**:
  - Does every state-modifying operation maintain core system invariants (`ensures Valid()`)?
  - Are `modifies` clauses restricted to the minimum required objects?
- [ ] **Undesired Freedom (Under-specification)**:
  - Can an adversary fulfill the postcondition using a trivial or malicious implementation (e.g. `ensures result >= 0` satisfied by always returning `0`)?
- [ ] **Vacuity — can each clause ever fire?**
  - For every `ensures A ==> B`, can `A` hold at all given the preconditions? Under `requires n >= 0`, the clause `ensures n < 0 ==> !ok` reads exactly like a real guard and is unreachable.
  - Is any clause of the form `... ==> true`, or literally `ensures true`? Those are satisfied by every implementation.
  - Does the body contain `assume false;` or `assert false;`, or the contract `requires false`? Any one of them makes the proof succeed against *any* implementation.
  - **This is the check a verifier cannot do for you.** A vacuous clause verifies in milliseconds and reports "0 errors"; the pass is indistinguishable from a real one. `vericoding score` flags the shapes it can recognise, but a clause it could not parse is reported as NOT ANALYSED, which is not a pass — read those yourself.
- [ ] **Does the proof cover what shipped?**
  - If the verified artifact is not the code that runs, say so explicitly. A proof about a twin is a regression oracle, not a guarantee about production.
