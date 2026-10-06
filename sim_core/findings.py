"""Deterministic findings — "why this score" in plain English.

Roadmap 1.2 (LOCKED) + the "rich verdict" pass: a fixed set of pure rules
over the ``summary()`` dict that explain the resilience score in
plain-English sentences — the verdict a non-technical person screenshots.
Same input dict ⇒ same list, always: no randomness, no clock, no AI.

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

Rich contract (the "rich verdict" pass)
---------------------------------------
Every finding carries, in addition to the original ``id`` / ``severity``
/ ``text`` / ``node`` keys:

- ``title``          — a short bold headline ("Weakest link: worker").
- ``why``            — 1–2 sentences on the *mechanism*: why this matters.
- ``impact``         — the quantified consequence (optional).
- ``evidence``       — the numbers behind the finding: a list of
                       ``{"label", "value"}`` pairs.
- ``recommendation`` — the concrete fix.

All of it is derived from the same summary dict, so the contract stays
deterministic: the same input always yields the same words. Consumers
that predate the rich fields (old CLI reports, older frontends) simply
ignore the extra keys.
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

    The rich fields (``title`` / ``why`` / ``impact`` / ``evidence`` /
    ``recommendation``) are optional in the type but present in practice
    on every finding produced by :func:`build_findings`; ``impact`` is
    the only one that may legitimately be absent.
    """

    id: str
    severity: Severity
    text: str
    title: NotRequired[str]
    why: NotRequired[str]
    impact: NotRequired[str]
    evidence: NotRequired[list[dict[str, str]]]
    recommendation: NotRequired[str]
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


def _num(value: Any) -> str:
    """Compact number for evidence chips: ``4.0 -> "4"``, ``3.5 -> "3.5"``."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        return "n/a"
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:g}"


def _pct(fraction: float, digits: int = 1) -> str:
    return f"{fraction * 100:.{digits}f}%"


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
        if util is None or util <= _UTILIZATION_CRIT:
            continue
        name = str(name)
        util_pct = _pct(util, 0)
        queue = _as_fraction(info.get("p95_queue"))
        rec = info.get("recommended_capacity")
        rec_ok = rec is not None and not isinstance(rec, bool) and isinstance(rec, (int, float))

        why = (
            f"At {util_pct} average utilization, {name} is effectively pinned — "
            f"almost every request in the path queues behind it, so the whole "
            f"architecture slows down, not just that box."
        )
        if queue is not None and queue > 0:
            why += f" At peak, {_num(queue)} request(s) were waiting in its queue."

        sla = _as_fraction(summary.get("sla_compliance"))
        if sla is not None:
            impact = (
                f"Everything downstream of {name} inherits the delay — the run's "
                f"SLA landed at {_pct(sla)}."
            )
        else:
            impact = (
                f"Everything downstream of {name} inherits the delay — this is "
                f"the wall the whole path eventually hits."
            )

        evidence: list[dict[str, str]] = [{"label": "avg utilization", "value": util_pct}]
        p95_util = _as_fraction(info.get("p95_utilization"))
        if p95_util is not None:
            evidence.append({"label": "p95 utilization", "value": _pct(p95_util, 0)})
        if queue is not None:
            evidence.append({"label": "p95 queue", "value": _num(queue)})
        if rec_ok:
            evidence.append({"label": "recommended capacity", "value": _num(rec)})

        if rec_ok:
            recommendation = (
                f"Size {name} up toward {_num(rec)} slots — the sizing pass's "
                f"recommendation from this run's actual demand."
            )
        else:
            recommendation = (
                f"Raise {name}'s capacity and re-run — the queue in front of it "
                f"is the bottleneck."
            )

        findings.append(
            {
                "id": "util_weak_link",
                "severity": "crit",
                "text": f"{name} is your weakest link (avg {util * 100:.0f}% util)",
                "title": f"Weakest link: {name}",
                "why": why,
                "impact": impact,
                "evidence": evidence,
                "recommendation": recommendation,
                "node": name,
            }
        )
    return findings


def _sla_breach(summary: dict[str, Any]) -> list[Finding]:
    """SLA compliance under the 95% floor is a crit — the run failed
    the promise, whatever the score says."""
    compliance = _as_fraction(summary.get("sla_compliance"))
    if compliance is None or compliance >= _SLA_FLOOR:
        return []
    pct = _pct(compliance)
    if compliance > 0:
        one_in = max(2, round(1.0 / (1.0 - compliance)))
        why = (
            f"SLA compliance is the promise to your users: at {pct}, about 1 in "
            f"{one_in} requests were too slow or never completed. That is a "
            f"product incident, not a tuning detail."
        )
    else:
        why = "No request met the SLA budget — the path is broken, not slow."

    requests = _as_fraction(summary.get("requests"))
    if requests is not None and requests > 0:
        impact = (
            f"If this were production, ~{(1.0 - compliance) * 100:.0f}% of "
            f"{int(requests)} requests would have violated the SLO."
        )
    else:
        impact = "In production, users would be seeing a wall of timeouts right now."

    evidence: list[dict[str, str]] = [
        {"label": "SLA compliance", "value": pct},
        {"label": "floor", "value": _pct(_SLA_FLOOR, 0)},
    ]
    p95 = _as_fraction(summary.get("p95_latency"))
    if p95 is not None:
        evidence.append({"label": "p95 latency", "value": f"{p95:.1f}s"})
    target = _as_fraction(summary.get("sla_target"))
    if target is not None:
        evidence.append({"label": "SLA target", "value": f"{target:.1f}s"})

    return [
        {
            "id": "sla_breach",
            "severity": "crit",
            "text": f"SLA compliance {pct} is below the 95% floor",
            "title": "SLA breach",
            "why": why,
            "impact": impact,
            "evidence": evidence,
            "recommendation": (
                "Fix the critical bottleneck first — a pinned node, a missing "
                "cache path — then re-run until compliance clears the 95% floor."
            ),
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
    one_in = max(2, round(1.0 / ratio))
    return [
        {
            "id": "retry_storm",
            "severity": "warn",
            "text": (
                f"{ratio * 100:.1f}% of requests needed a retry — "
                f"a retry storm amplifies the load"
            ),
            "title": "Retry storm",
            "why": (
                "Every retry is a second shot at the same wall: it doubles the "
                "load on exactly the component that is already struggling, and "
                "can turn a slow start into a timeout cascade."
            ),
            "impact": (
                f"Roughly 1 in {one_in} requests had to be re-run — the system "
                f"is amplifying its own load."
            ),
            "evidence": [
                {"label": "retries", "value": _num(retries)},
                {"label": "requests", "value": _num(requests)},
                {"label": "retry rate", "value": _pct(ratio)},
            ],
            "recommendation": (
                "Size up the struggling node and add sane timeouts with backoff — "
                "retries are a symptom, not the fix."
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
    multiple = p95 / sla
    finding: Finding = {
        "id": "p95_headroom",
        "severity": "warn",
        "text": (
            f"P95 {p95:.2f}s is {multiple:.1f}x the {sla:.2f}s SLA target — "
            f"no headroom left"
        ),
        "title": "No headroom left",
        "why": (
            f"P95 is what 95% of your users experience. At {multiple:.1f}× the "
            f"{sla:.1f}s budget, the tail is already over the line — one more "
            f"spike or incident tips the SLA itself."
        ),
        "evidence": [
            {"label": "p95 latency", "value": f"{p95:.2f}s"},
            {"label": "SLA target", "value": f"{sla:.2f}s"},
            {"label": "multiple", "value": f"{multiple:.1f}×"},
        ],
        "recommendation": (
            "Buy headroom where the queue forms — more capacity on the pinned "
            "node, or a cache in front of the hot path."
        ),
    }
    p99 = _as_fraction(summary.get("p99_latency"))
    if p99 is not None:
        finding["evidence"].append({"label": "p99 latency", "value": f"{p99:.2f}s"})
        if p99 > sla:
            finding["impact"] = (
                f"The worst 1% of requests (p99 {p99:.1f}s) were waiting "
                f"{p99 / sla:.0f}× the SLA budget."
            )
    return [finding]


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
        name = str(name)
        recommended = info.get("recommended_capacity")
        rec_ok = (
            recommended is not None
            and not isinstance(recommended, bool)
            and isinstance(recommended, (int, float))
        )
        text = f"{name} looks undersized"
        if rec_ok:
            text += f" (recommend size-to {recommended:.0f})"

        queue = _as_fraction(info.get("p95_queue"))
        if queue is not None and queue > 0:
            impact = f"Requests piled up: {_num(queue)} waiting in {name}'s queue at peak."
        else:
            impact = f"Demand in this run exceeded {name}'s capacity envelope."

        evidence: list[dict[str, str]] = []
        mean_util = _as_fraction(info.get("mean_utilization"))
        if mean_util is not None:
            evidence.append({"label": "mean utilization", "value": _pct(mean_util, 0)})
        if queue is not None:
            evidence.append({"label": "p95 queue", "value": _num(queue)})
        if rec_ok:
            evidence.append({"label": "recommended capacity", "value": _num(recommended)})

        if rec_ok:
            recommendation = (
                f"Size {name} to {_num(recommended)} slots — the sizing pass "
                f"already computed that from this run."
            )
        else:
            recommendation = (
                f"Raise {name}'s capacity and re-run — the run's peak demand "
                f"exceeds what it can absorb."
            )

        findings.append(
            {
                "id": "undersized",
                "severity": "warn",
                "text": text,
                "title": f"{name} is undersized",
                "why": (
                    f"This run's actual peak demand exceeds what {name} can "
                    f"absorb — the sizing pass flags it below the real workload, "
                    f"not the average."
                ),
                "impact": impact,
                "evidence": evidence,
                "recommendation": recommendation,
                "node": name,
            }
        )
    return findings


def _all_healthy(summary: dict[str, Any]) -> list[Finding]:
    """Nothing fired: the positive verdict (info) with its context."""
    finding: Finding = {
        "id": "healthy",
        "severity": "info",
        "text": "No structural weaknesses found in this run",
        "title": "Holding up",
        "why": (
            "SLA, completion, retries, headroom and every node's sizing all "
            "cleared their thresholds in this run."
        ),
        "recommendation": (
            "Re-run under a heavier incident or a bigger spike — "
            "'held up here' is not 'safe everywhere'."
        ),
    }
    evidence: list[dict[str, str]] = []
    sla = _as_fraction(summary.get("sla_compliance"))
    if sla is not None:
        evidence.append({"label": "SLA compliance", "value": _pct(sla)})
    p95 = _as_fraction(summary.get("p95_latency"))
    if p95 is not None:
        evidence.append({"label": "p95 latency", "value": f"{p95:.1f}s"})
    retries = _as_fraction(summary.get("total_retries"))
    if retries is not None:
        evidence.append({"label": "retries", "value": _num(retries)})
    if evidence:
        finding["evidence"] = evidence
    if sla is not None and p95 is not None:
        finding["impact"] = (
            f"It absorbed this load: {_pct(sla)} of requests met the SLA and "
            f"p95 stayed at {p95:.1f}s."
        )
    return [finding]


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
    ``Finding`` (same ids, severities, texts, rich fields, order).

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
        findings = _all_healthy(summary)

    findings.sort(key=lambda finding: _SEVERITY_RANK[finding["severity"]])
    return findings[:_MAX_FINDINGS]


def verdict_headline(summary: dict[str, Any], findings: list[Finding]) -> str:
    """One-sentence overall verdict for a run (deterministic).

    Reads the findings list produced by :func:`build_findings` plus the
    run's ``resilience_score`` and returns exactly one sentence — the
    line a non-technical reader screenshots. Pure: same inputs, same
    words. A zero-request run (``findings == []``) gets the honest
    "nothing to score" line instead of a fake verdict.
    """
    if not findings:
        return "The run produced no requests, so there is nothing to score yet."

    score = summary.get("resilience_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score_text = "n/a"
    else:
        score_text = f"{score:.0f}/100"

    crit = sum(1 for finding in findings if finding.get("severity") == "crit")
    warn = sum(1 for finding in findings if finding.get("severity") == "warn")

    if crit:
        sentence = (
            f"This architecture breaks under this load — {crit} critical "
            f"failure mode{'s' if crit != 1 else ''} found"
        )
        if warn:
            sentence += f", plus {warn} structural risk{'s' if warn != 1 else ''}"
        return (
            f"{sentence}. The score ({score_text}) can't be trusted until "
            f"the criticals are fixed."
        )
    if warn:
        return (
            f"This architecture bends but survives — {warn} structural "
            f"risk{'s' if warn != 1 else ''} is quietly eating SLA headroom "
            f"(score {score_text})."
        )
    return (
        f"This architecture holds — no structural weaknesses found "
        f"(score {score_text}). Re-run under a heavier incident to find "
        f"the real limit."
    )
