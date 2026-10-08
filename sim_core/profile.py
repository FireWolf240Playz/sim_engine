"""Multi-seed confidence profiling — worst / typical / best over N runs.

Roadmap item 1.1: a single seed can be lucky or unlucky; an architecture is
only as good as its worst day. Given the ``summary()`` dicts of several
runs of the *same* architecture (different seeds only), this module
reduces them to a confidence profile:

- ``worst``   — the value of the worst-performing run, per metric
- ``typical`` — the median across runs (even counts: mean of the two
  middle values, per :func:`statistics.median`)
- ``best``    — the value of the best-performing run, per metric
- ``score_spread`` — ``best.resilience_score - worst.resilience_score``
  (0.0 for a single run)
- ``per_run`` — one ``{seed, score, p95, sla, completion}`` row per run

Directionality (documented here and on the function):

- ``resilience_score`` / ``sla_compliance`` — higher is better, so
  ``worst = min``, ``best = max``.
- ``p95_latency`` / ``cost_per_completed_request`` — higher is *worse*,
  so ``worst = max`` (the slowest / most expensive day), ``best = min``.

That reads the way a non-technical stakeholder expects it: "worst" is
always the bad outcome for that metric, whatever the direction.

Runs whose value for a metric is ``None`` (e.g. zero-request runs have no
latency) are excluded from that metric's three-stat; if *every* run is
``None`` the three-stat is ``None`` and ``score_spread`` is ``None``.

This module deliberately imports **nothing** from the rest of
``sim_core``: it consumes plain dicts so it stays trivially
unit-testable and import cycles are impossible (same convention as
:mod:`sim_core.score`).
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: ``(summary key, higher_is_better)`` for every profiled metric.
#: Order here is the order the FE table renders them in.
_PROFILED_METRICS: Tuple[Tuple[str, bool], ...] = (
    ("resilience_score", True),
    ("sla_compliance", True),
    ("p95_latency", False),
    ("cost_per_completed_request", False),
)


def _three_stat(values: Sequence[float], higher_is_better: bool) -> Dict[str, Optional[float]]:
    """``{worst, typical, best}`` over *values* for one metric.

    *values* must already contain only non-``None`` entries. Empty input
    yields all-``None`` (the caller produces that case).
    """
    if higher_is_better:
        worst, best = min(values), max(values)
    else:
        worst, best = max(values), min(values)
    return {"worst": worst, "typical": statistics.median(values), "best": best}


def resilience_profile(
    summaries: List[Dict[str, Any]],
    seeds: Optional[List[Optional[int]]] = None,
) -> Dict[str, Any]:
    """Reduce N same-architecture run summaries to a confidence profile.

    Parameters
    ----------
    summaries:
        The ``MetricsCollector.summary()`` dict of each run, in run order.
        At least one is required; every entry must be a dict.
    seeds:
        Optional seed per summary (same length as ``summaries``). When
        given, each ``per_run`` row carries its seed; when omitted, the
        row's ``seed`` is ``None`` (and the top-level ``seeds`` is
        ``None``).

    Returns
    -------
    dict
        ``{n_runs, seeds, worst, typical, best, score_spread, per_run}``
        with ``worst/typical/best`` keyed by the profiled metric names
        (see :data:`_PROFILED_METRICS`) and ``per_run`` rows shaped
        ``{seed, score, p95, sla, completion}``.

    Raises
    ------
    ValueError
        If ``summaries`` is empty, an entry is not a dict, or ``seeds``
        is present with a length that does not match.
    """
    if not summaries:
        raise ValueError("resilience_profile needs at least one run summary")
    for index, summary in enumerate(summaries):
        if not isinstance(summary, dict):
            raise ValueError(f"summaries[{index}] is not a dict: {summary!r}")
    if seeds is not None and len(seeds) != len(summaries):
        raise ValueError(
            f"seeds has {len(seeds)} entries but {len(summaries)} summaries were given"
        )

    worst: Dict[str, Optional[float]] = {}
    typical: Dict[str, Optional[float]] = {}
    best: Dict[str, Optional[float]] = {}

    for key, higher_is_better in _PROFILED_METRICS:
        values = [float(s[key]) for s in summaries if s.get(key) is not None]
        if values:
            stat = _three_stat(values, higher_is_better)
        else:
            stat = {"worst": None, "typical": None, "best": None}
        worst[key] = stat["worst"]
        typical[key] = stat["typical"]
        best[key] = stat["best"]

    score_spread: Optional[float]
    if worst["resilience_score"] is not None and best["resilience_score"] is not None:
        score_spread = best["resilience_score"] - worst["resilience_score"]
    else:
        score_spread = None

    per_run: List[Dict[str, Any]] = []
    for index, summary in enumerate(summaries):
        per_run.append(
            {
                "seed": None if seeds is None else seeds[index],
                "score": summary.get("resilience_score"),
                "p95": summary.get("p95_latency"),
                "sla": summary.get("sla_compliance"),
                "completion": summary.get("completion_rate"),
            }
        )

    return {
        "n_runs": len(summaries),
        "seeds": None if seeds is None else list(seeds),
        "worst": worst,
        "typical": typical,
        "best": best,
        "score_spread": score_spread,
        "per_run": per_run,
    }


def typical_index(summaries: List[Dict[str, Any]]) -> int:
    """Index of the "typical" run: the middle run by resilience score.

    Runs are ordered by ``resilience_score`` ascending (``None`` scores
    last, original order breaking ties — stable), and the pick is
    position ``n // 2`` (0-based). For odd ``n`` that is exactly the
    median-score run; for even ``n`` it is the upper of the two middle
    runs. Deterministic: the same summaries always yield the same index.

    Raises
    ------
    ValueError
        If ``summaries`` is empty.
    """
    if not summaries:
        raise ValueError("typical_index needs at least one summary")
    order = sorted(
        range(len(summaries)),
        key=lambda i: (
            summaries[i].get("resilience_score") is None,
            summaries[i].get("resilience_score") or 0.0,
            i,
        ),
    )
    return order[len(order) // 2]


def timeseries_band(series: Sequence[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Per-tick P95 latency range across runs: the timeline's confidence band.

    *series* holds one ``timeseries()`` list per run. Ticks are matched by
    ``time`` (every run of one config samples on the same clock), in the
    order they first appear. Each entry is ``{time, p95_min, p95_max}``
    over the runs that completed a request in that tick; both are ``None``
    when none did, so the chart gaps instead of drawing a fake zero (the
    same rule as :meth:`MetricsCollector.timeseries`).
    """
    by_time: Dict[float, List[float]] = {}
    for ticks in series:
        for tick in ticks:
            values = by_time.setdefault(float(tick["time"]), [])
            p95 = tick.get("p95_latency")
            if p95 is not None:
                values.append(float(p95))
    return [
        {
            "time": time,
            "p95_min": min(values) if values else None,
            "p95_max": max(values) if values else None,
        }
        for time, values in by_time.items()
    ]
