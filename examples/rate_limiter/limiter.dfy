// RateLimiter Verification Example
// Implements a Token Bucket rate limiter formally proven to prevent exceeding capacity or borrowing tokens.

class RateLimiter {
  var tokens: int
  var capacity: int

  ghost predicate Valid()
    reads this
  {
    capacity > 0 &&
    tokens >= 0 &&
    tokens <= capacity
  }

  constructor(maxTokens: int)
    requires maxTokens > 0
    ensures Valid()
    ensures capacity == maxTokens
    ensures tokens == maxTokens
  {
    capacity := maxTokens;
    tokens := maxTokens;
  }

  method Allow(requested: int) returns (granted: bool)
    requires Valid()
    requires requested > 0
    modifies this
    ensures Valid()
    ensures granted <==> (old(tokens) >= requested)
    ensures granted ==> tokens == old(tokens) - requested
    ensures !granted ==> tokens == old(tokens)
  {
    if tokens >= requested {
      tokens := tokens - requested;
      granted := true;
    } else {
      granted := false;
    }
  }

  method Refill(refillAmount: int)
    requires Valid()
    requires refillAmount >= 0
    modifies this
    ensures Valid()
    ensures tokens == if old(tokens) + refillAmount > capacity then capacity else old(tokens) + refillAmount
  {
    if tokens + refillAmount > capacity {
      tokens := capacity;
    } else {
      tokens := tokens + refillAmount;
    }
  }
}
