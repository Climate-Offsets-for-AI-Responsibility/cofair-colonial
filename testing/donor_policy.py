from dataclasses import dataclass
from typing import Optional

# Fixed Cap = monthly, Excess Capcity = Leftover Tokens
# Pacings = rate of allowence opening
KINDS = ("fixed_cap", "excess_capacity")
PACINGS = ("none", "linear", "backloaded")


@dataclass
class DonorPolicy:
    kind: str
    cycle_days: int = 30
    cofair_cap: Optional[float] = None
    donor_max: Optional[float] = None
    buffer_frac: float = 0.10
    pacing: str = "none"

    # checking DonorPolicy requirements
    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.pacing not in PACINGS:
            raise ValueError(f"pacing must be one of {PACINGS}")
        if self.cycle_days <= 0:
            raise ValueError("cycle_days must be positive")
        if self.kind == "fixed_cap" and (self.cofair_cap is None or self.cofair_cap < 0):
            raise ValueError("fixed_cap needs a non-negative cofair_cap")
        if self.kind == "excess_capacity" and (self.donor_max is None or self.donor_max <= 0):
            raise ValueError("excess_capacity needs a positive donor_max")
        if not 0 <= self.buffer_frac < 1:
            raise ValueError("buffer_frac must be in [0, 1)")

# none = all tokens available
# linear = tokens used as gone
# backloaded = used more towards end
def pace_fraction(day_of_cycle: float, cycle_days: int, pacing: str) -> float:
    f = min(max(day_of_cycle / cycle_days, 0.0), 1.0)
    if pacing == "none":
        return 1.0
    if pacing == "linear":
        return f
    # backloaded
    return f ** 2

# Predicting donors future spending (spent so far / day) * (Rest of Cycle)
def forecast_donor_remaining(donor_used: float, day_of_cycle: float, cycle_days: int) -> float:
    if day_of_cycle <= 0:
        return 0.0
    rate = donor_used / day_of_cycle
    return rate * max(cycle_days - day_of_cycle, 0.0)

# Amount of money we have left for the billing cycle
# donor_forecast_remaining: optional override for the donor's expected spend over the rest of the cycle
def allowed_usage(policy: DonorPolicy, cofair_used: float, day_of_cycle: float, donor_used: float = 0.0, donor_forecast_remaining: Optional[float] = None,) -> float:

    pace = pace_fraction(day_of_cycle, policy.cycle_days, policy.pacing)

    if policy.kind == "fixed_cap":
        unlocked = policy.cofair_cap * pace
        return max(unlocked - cofair_used, 0.0)

    # excess_capacity
    if donor_forecast_remaining is None:
        donor_forecast_remaining = forecast_donor_remaining(
            donor_used, day_of_cycle, policy.cycle_days
        )
    safe_limit = policy.donor_max * (1 - policy.buffer_frac)
    projected_donor_total = donor_used + donor_forecast_remaining
    projected_excess = max(safe_limit - projected_donor_total, 0.0)

    # hard stop, total spend must stay under the safe limit.
    hard_room = max(safe_limit - donor_used - cofair_used, 0.0)

    paced_room = max(projected_excess * pace - cofair_used, 0.0)
    return min(paced_room, hard_room)


def donor_status(allowed: float, near_limit_threshold: float = 1.0) -> str:
    # We can update the limit threshold later
    if allowed <= 0:
        return "at_capacity"
    if allowed < near_limit_threshold:
        return "near_limit"
    return "available"

# To see what type of model in the current API key provider we can use
def can_route(policy, key_state, estimated_cost) -> bool:
    if estimated_cost < 0:
        raise ValueError("estimated cost can't be negative")
    return estimated_cost <= allowed_usage(policy, **key_state)
