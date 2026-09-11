# Example 1: Bank Account with Daily Limits

## 1. Natural Language Intent (User Requirements)
> "No user can withdraw more than their balance. Failed withdrawals don't change state. Daily withdrawal limits are enforced."

## 2. Formal Specification Mapping (`bank.dfy`)

| Natural Language Intent | Formal Dafny Contract |
| :--- | :--- |
| Balance non-negativity & consistency | `ghost predicate Valid() { balance >= 0 && dailyWithdrawn <= dailyLimit && ... }` |
| Valid withdrawal conditions | `ensures success <==> (old(balance) >= amount && old(dailyWithdrawn) + amount <= dailyLimit)` |
| Success state transition | `ensures success ==> balance == old(balance) - amount && dailyWithdrawn == old(dailyWithdrawn) + amount` |
| Failure state preservation | `ensures !success ==> balance == old(balance) && dailyWithdrawn == old(dailyWithdrawn)` |
| Frame safety (anti-side-effects) | `modifies this` |

## 3. Verification & SMT Proof
- **Solver Backend**: Z3 / SMT-LIB2 (Dafny 4.11.0)
- **Status**: Formally Verified (`0 errors`, proof verified in ~2.2s)
- **Audit Receipt**: See `bank.receipt.json`
