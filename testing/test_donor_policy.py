import pytest
from donor_policy import DonorPolicy, allowed_usage, can_route, donor_status, pace_fraction

# Testing different scenarios

def test_fixed_cap_full_budget_available_with_no_pacing():
    p = DonorPolicy(kind="fixed_cap", cofair_cap=20.0)
    assert allowed_usage(p, cofair_used=0, day_of_cycle=0) == 20.0


def test_fixed_cap_stops_at_cap():
    p = DonorPolicy(kind="fixed_cap", cofair_cap=20.0)
    assert allowed_usage(p, cofair_used=20.0, day_of_cycle=10) == 0.0
    assert allowed_usage(p, cofair_used=25.0, day_of_cycle=10) == 0.0


def test_fixed_cap_linear_pacing_halfway():
    p = DonorPolicy(kind="fixed_cap", cofair_cap=20.0, pacing="linear")
    assert allowed_usage(p, cofair_used=4.0, day_of_cycle=15) == pytest.approx(6.0)


def test_excess_uses_leftover_after_forecast_and_buffer():
    # Donor max $100, keep 10% buffer -> safe limit $90.
    # Day 15 of 30, donor spent $30 -> forecast another $30 -> projected $60.
    # Projected excess = 90 - 60 = 30, no pacing -> all $30 available.
    p = DonorPolicy(kind="excess_capacity", donor_max=100.0)
    assert allowed_usage(p, cofair_used=0, day_of_cycle=15, donor_used=30.0) == pytest.approx(30.0)


def test_excess_backloaded_takes_less_early():
    p = DonorPolicy(kind="excess_capacity", donor_max=100.0, pacing="backloaded")
    early = allowed_usage(p, cofair_used=0, day_of_cycle=6, donor_used=6.0)
    late = allowed_usage(p, cofair_used=0, day_of_cycle=27, donor_used=27.0)
    assert early < late


def test_excess_heavy_donor_leaves_nothing():
    # Donor on pace to spend $120 of a $100 max -> no excess.
    p = DonorPolicy(kind="excess_capacity", donor_max=100.0)
    assert allowed_usage(p, cofair_used=0, day_of_cycle=10, donor_used=40.0) == 0.0


def test_excess_hard_stop_respects_current_spend():
    # Custom forecast says donor will spend nothing more, but they have
    # already spent $85 of a $90 safe limit -> only $5 of room right now.
    p = DonorPolicy(kind="excess_capacity", donor_max=100.0)
    allowed = allowed_usage(p, cofair_used=0, day_of_cycle=5, donor_used=85.0,
                            donor_forecast_remaining=0.0)
    assert allowed == pytest.approx(5.0)


def test_excess_subtracts_what_cofair_already_used():
    p = DonorPolicy(kind="excess_capacity", donor_max=100.0)
    allowed = allowed_usage(p, cofair_used=10.0, day_of_cycle=15, donor_used=30.0)
    assert allowed == pytest.approx(20.0)


@pytest.mark.parametrize("pacing, day, expected", [
    ("none", 0, 1.0),
    ("linear", 15, 0.5),
    ("backloaded", 15, 0.25),
    ("linear", 45, 1.0),
])
def test_pace_fraction(pacing, day, expected):
    assert pace_fraction(day, 30, pacing) == pytest.approx(expected)


@pytest.mark.parametrize("allowed, status", [
    (0.0, "at_capacity"),
    (0.5, "near_limit"),
    (5.0, "available"),
])
def test_donor_status(allowed, status):
    assert donor_status(allowed) == status


@pytest.mark.parametrize("kwargs", [
    {"kind": "unknown"},
    {"kind": "fixed_cap"},
    {"kind": "excess_capacity"},
    {"kind": "fixed_cap", "cofair_cap": 20, "pacing": "fast"},
    {"kind": "excess_capacity", "donor_max": 100, "buffer_frac": 1.5},
])
def test_bad_policies_raise(kwargs):
    with pytest.raises(ValueError):
        DonorPolicy(**kwargs)

def test_can_route_fits_and_doesnt_fit():
    p = DonorPolicy(kind="fixed_cap", cofair_cap=20.0)
    state = {"cofair_used": 18.0, "day_of_cycle": 10}
    assert can_route(p, state, estimated_cost=1.5)
    assert not can_route(p, state, estimated_cost=3.0)

def test_can_route_rejects_negative_cost():
    p = DonorPolicy(kind="fixed_cap", cofair_cap=20.0)
    with pytest.raises(ValueError):
        can_route(p, {"cofair_used": 0, "day_of_cycle": 0}, estimated_cost=-1)