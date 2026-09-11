// BankAccount Verification Example
// Implements bank withdrawal with balance protection, state preservation on failure, and daily limit tracking.

class BankAccount {
  var balance: int
  var dailyWithdrawn: int
  var dailyLimit: int

  ghost predicate Valid()
    reads this
  {
    balance >= 0 &&
    dailyWithdrawn >= 0 &&
    dailyLimit >= 0 &&
    dailyWithdrawn <= dailyLimit
  }

  constructor(initialDeposit: int, limit: int)
    requires initialDeposit >= 0
    requires limit >= 0
    ensures Valid()
    ensures balance == initialDeposit
    ensures dailyLimit == limit
    ensures dailyWithdrawn == 0
  {
    balance := initialDeposit;
    dailyLimit := limit;
    dailyWithdrawn := 0;
  }

  method Withdraw(amount: int) returns (success: bool)
    requires Valid()
    requires amount > 0
    modifies this
    ensures Valid()
    ensures success <==> (old(balance) >= amount && old(dailyWithdrawn) + amount <= dailyLimit)
    ensures success ==> balance == old(balance) - amount && dailyWithdrawn == old(dailyWithdrawn) + amount
    ensures !success ==> balance == old(balance) && dailyWithdrawn == old(dailyWithdrawn)
  {
    if balance >= amount && dailyWithdrawn + amount <= dailyLimit {
      balance := balance - amount;
      dailyWithdrawn := dailyWithdrawn + amount;
      success := true;
    } else {
      success := false;
    }
  }

  method Deposit(amount: int)
    requires Valid()
    requires amount > 0
    modifies this
    ensures Valid()
    ensures balance == old(balance) + amount
    ensures dailyWithdrawn == old(dailyWithdrawn)
  {
    balance := balance + amount;
  }
}
