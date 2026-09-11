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
