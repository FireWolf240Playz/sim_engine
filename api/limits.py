"""How much work one API call may ask for.

The API runs its simulations inside the request, so an unbounded request is
an unbounded wait for that worker (and for everyone queued behind it). The
engine itself has a far higher ceiling (:data:`sim_core.config.MAX_EXPECTED_REQUESTS`);
these are the tighter limits for a call a browser is waiting on. The CLI is
not subject to them.
"""

from __future__ import annotations

from fastapi import HTTPException

from sim_core import SimulationConfig

#: Simulated requests one call may be expected to generate, summed over every
#: run it asks for (seeds, compare variants, sweep points).
MAX_REQUESTS_PER_CALL: int = 200_000

#: Values one sweep may try (each is a full run).
MAX_SWEEP_VALUES: int = 25

#: Patches one what-if may try (each is a full run).
MAX_WHAT_IF_VARIANTS: int = 25


def check_budget(config: SimulationConfig, runs: int) -> None:
    """Refuse (422) a call whose runs add up to more than the API serves.

    The estimate is the traffic profile's expected request count over the
    run, times the number of runs. Variants that change the traffic itself
    are estimated from the baseline.
    """
    per_run = config.traffic.expected_requests(config.duration)
    total = per_run * runs
    if total <= MAX_REQUESTS_PER_CALL:
        return
    count = "1 run" if runs == 1 else f"{runs} runs"
    raise HTTPException(
        status_code=422,
        detail=(
            f"this call would simulate about {total:,.0f} requests "
            f"({per_run:,.0f} per run x {count}); the API serves at most "
            f"{MAX_REQUESTS_PER_CALL:,} per call. Shorten the run, lower the traffic, "
            "or ask for fewer runs. The CLI has no such limit."
        ),
    )
