"""Deterministic architecture scoring and cost extrapolation.

Phase 2 of the Eleven plan (#5 + #6). Everything here is *pure math over a
``MetricsCollector.summary()`` dict* — no simulation, no new dependencies,
no hidden state. Same summary in ⇒ same score out, always. That property is
what lets the CLI, the future API, and the frontend all reuse these
functions verbatim.

Public functions:

- :func:`resilience_score` — 0..100 resilience number (SLA-dominant blend).
- :func:`cost_grade` — 'A'..'F' right-sizing / cost-efficiency grade.
- :func:`cost_extrapolation` — ``{hour, month, year}`` steady-state
  projection, honestly labeled as "same load sustained 24/7".
- :func:`cost_per_completed_request` — headline unit-cost context.
- :func:`score_headline` — the one-line stakeholder summary.
- :func:`score_color` — badge color for the report PNG.

This module deliberately imports **nothing** from the rest of ``sim_core``:
it consumes plain dicts so it stays trivially unit-testable and import
cycles are impossible.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Weights and bands — the single source of truth for the scoring formulas.
# Documented here and in each function's docstring; change deliberately.
# ---------------------------------------------------------------------------

#: Weights of the resilience base blend (sum to 1.0). SLA compliance is the
#: dominant signal — under chaos requests usually still complete, they just
#: get slow, so completion alone would overstate health.
SLA_WEIGHT: float = 0.6
COMPLETION_WEIGHT: float = 0.4

#: Maximum points deducted for hard failures (request never completed).
#: On top of — not instead of — the completion-rate term, because a dropped
#: request is a worse outcome than a slow one.
FAILED_PENALTY: float = 30.0

#: Maximum points deducted for retry pressure (timeouts that had to be
#: retried). Capped at 1.0 × penalty regardless of retry volume.
RETRY_PENALTY: float = 10.0

#: Maximum points deducted when p95 latency runs past the SLA target.
#: Scaled by how far past: 100% over the target costs the full 10 points.
HEADROOM_PENALTY: float = 10.0

#: Per-component credit used by :func:`cost_grade`. Undersized scores 0:
#: it is a *reliability* risk, not merely a cost question. Oversized scores
#: 0.5: the workload is healthy but money is sitting idle.
SIZING_CREDIT: Dict[str, float] = {
    "right_sized": 1.0,
    "oversized": 0.5,
    "undersized": 0.0,
}

#: Blend of the two cost_grade inputs.
SIZING_SHARE: float = 0.7
COST_SHARE: float = 0.3

#: Grade bands over the 0..1 composite (lower bound inclusive).
GRADE_BANDS = ((0.90, "A"), (0.75, "B"), (0.55, "C"), (0.35, "D"))
DEFAULT_GRADE = "F"

#: Months/years expressed in hours for the steady-state projection.
HOURS_PER_MONTH: float = 24.0 * 30.0
HOURS_PER_YEAR: float = 24.0 * 365.0


# ---------------------------------------------------------------------------
# Resilience score
# ---------------------------------------------------------------------------

def resilience_score(summary: Dict[str, Any]) -> Optional[float]:
    """Compute the 0..100 resilience score from a summary dict.

    Formula (all inputs read from *summary*, all fractions in 0..1)::

        score = 100 * (0.6 * sla_compliance + 0.4 * completion_rate)
              - 30 * (failed_requests / requests)
              - 10 * min(1, total_retries / requests)
              - 10 * min(1, max(0, p95_latency - sla_target) / sla_target)

    clamped to ``[0, 100]`` and rounded to one decimal.

    Weight rationale (documented contract):

    - **SLA compliance (60 pts)** is dominant: it is the customer-visible
      quality number and the one a resilience test exists to protect.
    - **Completion rate (40 pts)**: hard availability — a request that never
      lands is the worst outcome.
    - **Failed-request penalty (up to 30 pts)**: layered on top of the
      completion term so drops hit harder than slowness.
    - **Retry penalty (up to 10 pts)**: timeouts that had to be retried mean
      the system was on the edge, even when it recovered.
    - **p95-vs-SLA headroom penalty (up to 10 pts)**: a 95th-percentile
      latency *above* the SLA budget means the tail is already violating
      the SLO, even if the mean looks fine. No penalty when p95 is inside
      the target (no bonus either — headroom is context, not credit).

    Returns ``None`` when the summary has no requests (nothing to score).
    Deterministic: the same dict always yields the same score.
    """
    n = summary.get("requests") or 0
    if n <= 0:
        return None

    sla = summary.get("sla_compliance")
    if sla is None:
        sla = 1.0  # no SLA configured ⇒ the engine counts every request as met
    completion = summary.get("completion_rate")
    if completion is None:
        completion = 0.0

    score = 100.0 * (SLA_WEIGHT * sla + COMPLETION_WEIGHT * completion)

    failed = summary.get("failed_requests") or 0
    score -= FAILED_PENALTY * min(1.0, failed / n)

    retries = summary.get("total_retries") or 0
    score -= RETRY_PENALTY * min(1.0, retries / n)

    sla_target = summary.get("sla_target")
    p95 = summary.get("p95_latency")
    if sla_target and p95 is not None and p95 > sla_target:
        overage = (p95 - sla_target) / sla_target
        score -= HEADROOM_PENALTY * min(1.0, overage)

    return round(min(100.0, max(0.0, score)), 1)


# ---------------------------------------------------------------------------
# Cost grade
# ---------------------------------------------------------------------------

def cost_grade(summary: Dict[str, Any]) -> Optional[str]:
    """Compute the 'A'..'F' cost grade from a summary dict.

    The grade is a blend of *sizing quality* and *cost efficiency*::

        composite = 0.7 * sizing_quality + 0.3 * cost_efficiency

    - **sizing_quality** — mean per-component credit from
      ``component_sizing``: ``right_sized = 1.0``, ``oversized = 0.5``,
      ``undersized = 0.0``. Undersized is scored worst on purpose: it is a
      reliability risk, not just a cost one.
    - **cost_efficiency** — ``1 - (share of total cost sitting on oversized
      components)``, from ``cost_breakdown_by_component`` +
      ``component_sizing``. When no rates are configured (total cost 0),
      it falls back to ``1 - (oversized components / all components)`` so
      the grade is still meaningful.

    Bands (composite, lower bound inclusive): **A** >= 0.90, **B** >= 0.75,
    **C** >= 0.55, **D** >= 0.35, else **F**.

    This is a directional FinOps signal over the sampled run — deliberately
    coarse, workload-independent, and fully deterministic. Returns ``None``
    when the summary carries no sizing data (nothing to grade).
    """
    sizing = summary.get("component_sizing") or {}
    if not sizing:
        return None

    statuses = [str(info.get("status")) for info in sizing.values()]
    credits = [SIZING_CREDIT.get(status, 0.5) for status in statuses]
    sizing_quality = sum(credits) / len(credits)

    total_cost = summary.get("total_cost") or 0.0
    breakdown = summary.get("cost_breakdown_by_component") or {}
    if total_cost > 0.0:
        oversized_cost = sum(
            float(cost)
            for name, cost in breakdown.items()
            if str((sizing.get(name) or {}).get("status")) == "oversized"
        )
        cost_efficiency = 1.0 - oversized_cost / total_cost
    else:
        oversized_share = sum(1 for status in statuses if status == "oversized") / len(statuses)
        cost_efficiency = 1.0 - oversized_share

    composite = SIZING_SHARE * sizing_quality + COST_SHARE * cost_efficiency
    for lower_bound, grade in GRADE_BANDS:
        if composite >= lower_bound:
            return grade
    return DEFAULT_GRADE


# ---------------------------------------------------------------------------
# Cost extrapolation
# ---------------------------------------------------------------------------

def cost_extrapolation(total_cost: float, duration: float) -> Dict[str, float]:
    """Linear steady-state projection of a run's cost to hour/month/year.

    ``hour = total_cost / duration * 3600``, ``month = hour * 24 * 30``,
    ``year = hour * 24 * 365``.

    Honest labeling: this is a **steady-state assumption** — it projects as
    if the *same load* were sustained 24/7 for the whole period. It is an
    extrapolation for planning conversations, not a bill; bursty or
    diurnal workloads will cost less, sustained saturation will cost more.

    Values are returned unrounded so ``hour * 24 * 30 == month`` holds
    exactly (callers format for display).
    """
    if duration <= 0.0:
        raise ValueError(f"duration must be > 0, got {duration!r}")
    if total_cost < 0.0:
        raise ValueError(f"total_cost must be >= 0, got {total_cost!r}")
    hour = (total_cost / duration) * 3600.0
    return {
        "hour": hour,
        "month": hour * HOURS_PER_MONTH,
        "year": hour * HOURS_PER_YEAR,
    }


def cost_per_completed_request(summary: Dict[str, Any]) -> Optional[float]:
    """Total cost divided by the number of *completed* (successful) requests.

    The unit-cost number a non-technical stakeholder actually thinks in
    ("what does one request cost me?"). ``None`` when there is no cost
    data or no completed request to divide by.
    """
    total_cost = summary.get("total_cost") or 0.0
    if total_cost <= 0.0:
        return None
    n = summary.get("requests") or 0
    completed = n - (summary.get("failed_requests") or 0)
    if completed <= 0:
        return None
    return total_cost / completed


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------

def _fmt_money_compact(value: float) -> str:
    """``31536000 -> '31.5k'`` style for the headline's yearly figure."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,.0f}"


def score_headline(summary: Dict[str, Any]) -> str:
    """One-line stakeholder headline from a summary dict.

    Example with the cost split present::

        Resilience 89/100 | Cost grade C | $2,592,000/mo (steady $2,089,000/mo) | $31.5M/yr

    The monthly figure is the *under-load* bill (base + metered); the
    parenthesized figure is the *steady* bill — the same bill with none of
    the load/chaos-driven metered part on top (provisioned base only). When
    ``steady_state_cost_extrapolation`` is absent (older summaries, or no
    rates), only the monthly figure is shown.

    Degrades gracefully: any missing piece is rendered as ``n/a`` (or the
    cost parts are dropped entirely when no rates are configured), so the
    line is always meaningful — even for a run with zero requests.
    """
    parts: List[str] = []

    score = summary.get("resilience_score")
    parts.append(f"Resilience {score:.0f}/100" if score is not None else "Resilience n/a")

    grade = summary.get("cost_grade")
    parts.append(f"Cost grade {grade}" if grade else "Cost grade n/a")

    extrapolation = summary.get("cost_extrapolation")
    if extrapolation:
        monthly = f"${extrapolation['month']:,.0f}/mo"
        steady = summary.get("steady_state_cost_extrapolation")
        if steady:
            monthly += f" (steady ${steady['month']:,.0f}/mo)"
        parts.append(monthly)
        parts.append(f"${_fmt_money_compact(extrapolation['year'])}/yr")

    return " | ".join(parts)


def score_color(score: float) -> str:
    """Badge color for a resilience score (report PNG, future frontend)."""
    if score >= 75.0:
        return "#2f855a"  # green
    if score >= 50.0:
        return "#b7791f"  # amber
    return "#e53e3e"  # red


# ---------------------------------------------------------------------------
# Score explanation — "how the score is built", point by point
# ---------------------------------------------------------------------------

#: Plain-English bands over the 0..100 resilience score (lower bound
#: inclusive). Distinct from the *color* bands in :func:`score_color`:
#: the color is a 3-way traffic light, the band is a 4-way plain-English
#: verdict word the frontend renders next to the score.
SCORE_BANDS: tuple[tuple[float, str], ...] = (
    (80.0, "Resilient"),
    (65.0, "Solid"),
    (50.0, "At risk"),
)
DEFAULT_BAND = "Fragile"


def score_band(score: float) -> str:
    """Plain-English band word for a 0..100 resilience score.

    Bands (lower bound inclusive): **Resilient** >= 80, **Solid** >= 65,
    **At risk** >= 50, else **Fragile**. Pure and deterministic.
    """
    for lower_bound, band in SCORE_BANDS:
        if score >= lower_bound:
            return band
    return DEFAULT_BAND


def score_explanation(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Point-by-point decomposition of :func:`resilience_score` in plain terms.

    Mirrors the scoring formula exactly — the same inputs, the same math —
    so for any summary this function can score, ``explanation["score"] ==
    resilience_score(summary)`` holds. It exists to answer the question the
    raw score can't: *why this number?*::

        {
            "score": 89.2,        # == resilience_score(summary), clamped
            "clamped": False,     # True when the raw blend hit the 0..100 rail
            "band": "Solid",      # score_band(score) — the plain-English word
            "terms": [
                {"label": "SLA compliance", "points": 54.0,
                 "detail": "90.0% of requests met the SLA × 60 pts"},
                ...
            ],
        }

    ``terms`` is the ordered list of the five formula contributions
    (SLA + completion − failed − retries − headroom), each with a signed
    ``points`` value and a one-line ``detail``. Summing the *unrounded*
    terms reproduces the raw score before the 0..100 clamp; the displayed
    per-term points are rounded to one decimal (so their printed sum can
    differ from ``score`` by a rounding fraction at most, and by more
    only when ``clamped`` is true). Deterministic: same dict, same words.

    Returns ``None`` when the summary has no requests (nothing to score —
    mirrors :func:`resilience_score`).
    """
    n = summary.get("requests") or 0
    if n <= 0:
        return None

    sla = summary.get("sla_compliance")
    if sla is None:
        sla = 1.0  # no SLA configured ⇒ the engine counts every request as met
    completion = summary.get("completion_rate")
    if completion is None:
        completion = 0.0

    sla_points = 100.0 * SLA_WEIGHT * sla
    completion_points = 100.0 * COMPLETION_WEIGHT * completion

    failed = summary.get("failed_requests") or 0
    failed_points = FAILED_PENALTY * min(1.0, failed / n)

    retries = summary.get("total_retries") or 0
    retry_points = RETRY_PENALTY * min(1.0, retries / n)

    sla_target = summary.get("sla_target")
    p95 = summary.get("p95_latency")
    headroom_points = 0.0
    if sla_target and p95 is not None and p95 > sla_target:
        headroom_points = HEADROOM_PENALTY * min(1.0, (p95 - sla_target) / sla_target)

    raw = (
        sla_points
        + completion_points
        - failed_points
        - retry_points
        - headroom_points
    )
    clamped = (min(100.0, max(0.0, raw)) != raw)
    final = round(min(100.0, max(0.0, raw)), 1)

    terms: List[Dict[str, Any]] = [
        {
            "label": "SLA compliance",
            "points": round(sla_points, 1),
            "detail": f"{sla * 100:.1f}% of requests met the SLA × 60 pts",
        },
        {
            "label": "Completion",
            "points": round(completion_points, 1),
            "detail": f"{completion * 100:.1f}% of requests completed × 40 pts",
        },
        {
            "label": "Failed requests",
            "points": round(-failed_points, 1),
            "detail": (
                f"{failed} of {n} requests never completed — up to −30 pts"
                if failed
                else "no request drops"
            ),
        },
        {
            "label": "Retry pressure",
            "points": round(-retry_points, 1),
            "detail": (
                f"{retries} retries across {n} requests — up to −10 pts"
                if retries
                else "no retries"
            ),
        },
    ]
    if headroom_points:
        overage = (p95 - sla_target) / sla_target  # both positive here, p95 > target
        terms.append(
            {
                "label": "P95 headroom",
                "points": round(-headroom_points, 1),
                "detail": (
                    f"p95 {p95:.1f}s runs {overage * 100:.0f}% over the "
                    f"{sla_target:.1f}s SLA budget — up to −10 pts"
                ),
            }
        )
    else:
        terms.append(
            {
                "label": "P95 headroom",
                "points": 0.0,
                "detail": "p95 sits inside the SLA budget — no headroom penalty",
            }
        )

    return {
        "score": final,
        "clamped": clamped,
        "band": score_band(final),
        "terms": terms,
    }
