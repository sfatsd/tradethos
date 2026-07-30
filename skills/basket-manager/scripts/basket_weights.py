#!/usr/bin/env python3
"""Whole-number target weights for a basket.

A basket's target weights are whole numbers of percent that always sum to
exactly 100. This module owns that arithmetic. It performs no input or output.
"""

MAX_HOLDINGS = 100
TOTAL_PERCENT = 100

FILL_PROPORTIONAL = "proportional"
FILL_EQUAL = "equal"
FILL_MODES = (FILL_PROPORTIONAL, FILL_EQUAL)


class WeightError(ValueError):
    """A weight set breaks one of this module's rules.

    Every weight must be a number above 0; a basket holds at most
    MAX_HOLDINGS symbols; and no holding may round down to 0 percent once
    the set is scaled to sum to exactly 100.
    """


def _check_input(weights):
    """Reject a weight that is not a positive number."""
    for symbol, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WeightError(
                "The weight for %s is not a number: %r" % (symbol, value)
            )
        if value <= 0:
            raise WeightError(
                "The weight for %s must be above 0, but it is %s" % (symbol, value)
            )
    if len(weights) > MAX_HOLDINGS:
        raise WeightError(
            "A basket holds at most %d holdings, but this set holds %d"
            % (MAX_HOLDINGS, len(weights))
        )


def _largest_remainder(exact, target):
    """Round exact shares to whole numbers that sum to target.

    Each holding takes the whole part of its exact share. The shortfall then
    goes one percent at a time to the holdings with the largest fractional
    part. A tie breaks by symbol, in alphabetical order.
    """
    result = {}
    for symbol, value in exact.items():
        result[symbol] = int(value)

    shortfall = target - sum(result.values())
    ranked = sorted(exact, key=lambda s: (-(exact[s] - int(exact[s])), s))
    for symbol in ranked[:shortfall]:
        result[symbol] += 1
    return result


def normalize_weights(weights):
    """Scale a weight set to whole numbers that sum to exactly 100.

    The input may hold decimals, and it may be a ratio such as {'NVDA': 2,
    'MSFT': 1}. The output holds whole numbers between 1 and 100.

    Raises WeightError when an input is not a positive number, when the set
    holds more than MAX_HOLDINGS symbols, or when a result would fall to 0.
    """
    if not weights:
        return {}

    _check_input(weights)

    total = float(sum(weights.values()))
    exact = {}
    for symbol, value in weights.items():
        exact[symbol] = value * TOTAL_PERCENT / total

    result = _largest_remainder(exact, TOTAL_PERCENT)

    zeros = sorted(s for s, v in result.items() if v < 1)
    if zeros:
        raise WeightError(
            "These holdings would fall to 0 percent: %s. "
            "Remove them, or give them a larger share." % ", ".join(zeros)
        )
    return result


def refill(others, room, mode=FILL_PROPORTIONAL):
    """Distribute `room` percent across `others`.

    FILL_PROPORTIONAL keeps the ratios between the other holdings.
    FILL_EQUAL gives every other holding the same weight.

    Raises WeightError when the mode is unknown, or when `room` is too small
    to give every holding at least one percent.
    """
    if mode not in FILL_MODES:
        raise WeightError(
            "The fill mode %r is unknown. Use one of: %s"
            % (mode, ", ".join(FILL_MODES))
        )
    if not others:
        return {}
    if room < len(others):
        raise WeightError(
            "%d percent cannot cover %d holdings, because every holding needs "
            "at least 1 percent. Remove a holding instead." % (room, len(others))
        )

    if mode == FILL_EQUAL:
        source = {}
        for symbol in others:
            source[symbol] = 1.0
    else:
        source = dict(others)

    total = float(sum(source.values()))
    exact = {}
    for symbol, value in source.items():
        exact[symbol] = value * room / total

    result = _largest_remainder(exact, room)

    # `room >= len(others)` is not enough. A skewed set can still round a
    # small holding to 0: {MSFT: 49, AAPL: 1} with room 2 gives 1.96 and 0.04,
    # and the shortfall goes to MSFT.
    zeros = sorted(s for s, v in result.items() if v < 1)
    if zeros:
        raise WeightError(
            "These holdings would fall to 0 percent: %s. Remove them, or give "
            "the named holding a smaller weight." % ", ".join(zeros))
    return result
