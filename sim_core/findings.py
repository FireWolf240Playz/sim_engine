"""Deterministic findings — "why this score" in plain English.

Roadmap 1.2 (LOCKED): a fixed set of pure rules over the ``summary()``
dict that explain the resilience score in plain-English sentences —
the verdict a non-technical person screenshots. Same input dict ⇒ same
list, always: no randomness, no clock, no AI.

This module deliberately imports **nothing** from the rest of
``sim_core``: it consumes plain dicts so it stays trivially
unit-testable and import cycles are impossible (same convention as
:mod:`sim_core.score` and :mod:`sim_core.profile`).

Severity model (shared with the frontend):

- ``crit`` — the run failed in a way the architecture should not allow
  (SLA breach, a node pinned past 90% utilisation).
- ``warn`` — the run survived but with a structural risk
  (retry storm, no p95 headroom, an undersized node).
- ``info`` — nothing fired: the architecture held up.

Findings are sorted by severity (crit → warn → info; stable within a
tier, in rule order) and capped at 5, per the roadmap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, NotRequired, TypedDict

#: Severity tokens — the exact strings the frontend maps to colors/icons.
Severity = Literal["info", "warn", "crit"]


class Finding(TypedDict):
    """One plain-English verdict line about this run.

    ``id`` is a stable slug (asserted verbatim in tests and usable by
    the frontend for styling/keys). ``node`` is present only for
    per-node findings (it names the component, for the node inspector
    link in roadmap 1.4); global findings omit it.
    """

    id: str
    severity: Severity
    text: str
    node: NotRequired[str]


#: Sort rank per severity: lower sorts first.
_SEVERITY_RANK: dict[str, int] = {"crit": 0, "warn": 1, "info": 2}

#: Hard cap on how many findings a run reports (roadmap 1.2).
_MAX_FINDINGS = 5

#: Utilisation (fraction) above which a node is "your weakest link".
_UTILIZATION_CRIT = 0.90

#: SLA compliance (fraction) below which the run is a breach.
_SLA_FLOOR = 0.95

#: retries / requests above which the run is amplifying its own load.
_RETRY_STORM_RATIO = 0.02

#: p95 / SLA multiple above which no headroom is left.
_P95_HEADROOM_RATIO = 1.5


def _as_fraction(value: Any) -> float | None:
    """A finite non-negative float, or ``None`` when the value is unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return value


def _util_weak_link(summary: dict[str, Any]) -> list[Finding]:
    """Any node whose avg utilisation is past 90% is a crit — it is the
    wall the whole path eventually hits."""
    sizing = summary.get("component_sizing")
    if not isinstance(sizing, dict):
        return []
    findings: list[Finding] = []
    for name, info in sizing.items():
        if not isinstance(info, dict):
            continue
        util = _as_fraction(info.get("mean_utilization"))
        if util is not None and util > _UTILIZATION_CRIT:
            findings.append(
                {
                    "id": "util_weak_link",
                    "severity": "crit",
                    "text": f"{name} is your weakest link (avg {util * 100:.0f}% util)",
                    "node": str(name),
                }
            )
    return findings


def _sla_breach(summary: dict[str, Any]) -> list[Finding]:
    """SLA compliance under the 95% floor is a crit — the run failed
    the promise, whatever the score says."""
    compliance = _as_fraction(summary.get("sla_compliance"))
    if compliance is None or compliance >= _SLA_FLOOR:
        return []
    return [
        {
            "id": "sla_breach",
            "severity": "crit",
            "text": f"SLA compliance {compliance * 100:.1f}% is below the 95% floor",
        }
    ]


def _retry_storm(summary: dict[str, Any]) -> list[Finding]:
    """More than 2% of requests needed a retry: the run is amplifying
    its own load, and every retry is a second shot at the same wall."""
    requests = _as_fraction(summary.get("requests"))
    retries = _as_fraction(summary.get("total_retries"))
    if requests is None or retries is None or requests <= 0:
        return []
    ratio = retries / requests
    if ratio <= _RETRY_STORM_RATIO:
        return []
    return [
        {
            "id": "retry_storm",
            "severity": "warn",
            "text": (
                f"{ratio * 100:.1f}% of requests needed a retry — "
                f"a retry storm amplifies the load"
            ),
        }
    ]


def _p95_headroom(summary: dict[str, Any]) -> list[Finding]:
    """P95 past 1.5x the SLA target: the tail already runs out of road —
    one more incident and the SLA breaks."""
    p95 = _as_fraction(summary.get("p95_latency"))
    sla = _as_fraction(summary.get("sla_target"))
    if p95 is None or sla is None or sla <= 0 or p95 <= _P95_HEADROOM_RATIO * sla:
        return []
    return [
        {
            "id": "p95_headroom",
            "severity": "warn",
            "text": (
                f"P95 {p95:.2f}s is {p95 / sla:.1f}x the {sla:.2f}s SLA target — "
                f"no headroom left"
            ),
        }
    ]


def _undersized(summary: dict[str, Any]) -> list[Finding]:
    """A node the sizing pass flags ``undersized`` is a warn — it is
    below the demand the run actually produced."""
    sizing = summary.get("component_sizing")
    if not isinstance(sizing, dict):
        return []
    findings: list[Finding] = []
    for name, info in sizing.items():
        if not isinstance(info, dict) or info.get("status") != "undersized":
            continue
        recommended = info.get("recommended_capacity")
        text = f"{name} looks undersized"
        if isinstance(recommended, (int, float)) and not isinstance(recommended, bool):
            text += f" (recommend size-to {recommended:.0f})"
        findings.append(
            {
                "id": "undersized",
                "severity": "warn",
                "text": text,
                "node": str(name),
            }
        )
    return findings


def _all_healthy() -> list[Finding]:
    """Nothing fired: the one-line positive verdict (info)."""
    return [
        {
            "id": "healthy",
            "severity": "info",
            "text": "No structural weaknesses found in this run",
        }
    ]


#: The rule order. Within one severity tier this is the output order
#: (the sort is stable), so rule order is a documented priority.
_RULES: tuple[Callable[[dict[str, Any]], list[Finding]], ...] = (
    _util_weak_link,
    _sla_breach,
    _retry_storm,
    _p95_headroom,
    _undersized,
)


def build_findings(summary: dict[str, Any]) -> list[Finding]:
    """Reduce one run's ``summary()`` dict to its plain-English verdict.

    Pure and deterministic: the same dict always yields the same list of
    ``Finding`` (same ids, severities, texts, order).

    Parameters
    ----------
    summary:
        The ``MetricsCollector.summary()`` dict of one run.

    Returns
    -------
    list[Finding]
        At most 5 findings, sorted crit → warn → info (stable within a
        tier, in rule order). A run with zero requests yields ``[]`` —
        no samples, no verdict (mirrors the no-single-point rule).
        When no rule fires, a single ``info`` finding
        ("No structural weaknesses found in this run") is returned.

    Raises
    ------
    ValueError
        If ``summary`` is not a dict.
    """
    if not isinstance(summary, dict):
        raise ValueError(
            f"build_findings expects a summary dict, got {type(summary).__name__}"
        )

    requests = _as_fraction(summary.get("requests"))
    if requests is None or requests <= 0:
        return []

    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(summary))

    if not findings:
        findings = _all_healthy()

    findings.sort(key=lambda finding: _SEVERITY_RANK[finding["severity"]])
    return findings[:_MAX_FINDINGS]
