"""Right-sizing suggestions (roadmap 1.3) — "fix it" as a pure function.

``build_suggestions`` turns a run's ``summary()`` (specifically its
``component_sizing`` block) plus the topology's node dicts into a
deterministic list of capacity changes (rules pinned in
``tests/test_suggestions.py``):

- **undersized** → ``max(current + 1, ceil(current * mean_util / 0.8))``:
  size so the observed load sits at an 80% target. ``recommended_capacity``
  is deliberately *not* the proposal — it is the p99 of in-use + queued
  demand, so a saturated node's backlog inflates it (a 2-slot worker reads
  138).
- **oversized**  → one 25% step down, ``max(1, floor(current * 0.75))``,
  and only when it is a real cut whose predicted utilisation
  (``mean_util * current / proposed``) stays at or under 0.8 — a cut that
  would flip the node to undersized is dropped. Repeated apply-and-rerun
  cycles converge; nothing ever flips to undersized.
- **right_sized** → no suggestion.

Ordering: raises first, then cuts; node name within each group. Same input
⇒ same list, always. ``summary()["suggestions"]`` carries the list (attached
in :meth:`sim_core.engine.CloudSimulator.run`), so the CLI, the API and
every multi-seed run get it with no API shape change. No SimPy, no re-run,
no mutation of the inputs.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, TypedDict

#: Healthy operating point: size so the observed mean utilisation lands here.
TARGET_UTILIZATION = 0.8

#: One oversized step = 25% down. Repeated applications converge.
_OVERSIZED_STEP_FRACTION = 0.25

_MIN_CAPACITY = 1

#: 24h x 30d — the repo's planning month, in hours.
_HOURS_PER_MONTH = 720


def _is_number(value: Any) -> bool:
    """True for real ints/floats (bools excluded — ``isinstance(True, int)``)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class Suggestion(TypedDict):
    """One proposed capacity change for a single node.

    ``node`` names the topology node; ``param`` is the config field to
    patch (v1: always ``max_capacity``); ``current`` / ``proposed`` are
    the before/after capacities. ``reason`` is a deterministic plain-English
    line (same input ⇒ same words) that the CLI and FE print verbatim,
    quoting the observed utilisation.

    ``est_monthly_delta`` prices the *provisioned slots only* —
    ``cost_per_hour * (proposed - current) * 720`` — because metered cost
    does not scale with capacity. It is ``None`` when the node carries no
    rate (``cost_per_hour`` absent or 0). The key is always present so
    consumers can rely on the exact shape.
    """

    node: str
    param: str
    current: int
    proposed: int
    reason: str
    est_monthly_delta: float | None


def build_suggestions(
    nodes: Iterable[Mapping[str, Any]], summary: Mapping[str, Any]
) -> list[Suggestion]:
    """Deterministic right-sizing suggestions for one run.

    Pure over ``(nodes, summary)`` — ``nodes`` are the topology's node
    dicts (``ComponentConfig.model_dump(mode="json")`` each, carrying
    ``name`` / ``max_capacity`` / ``cost_per_hour``), ``summary`` the run's
    headline dict. Reads ``summary["component_sizing"]`` (status + mean
    utilisation) and the node's current ``max_capacity``. Returns an empty
    list when the summary has no usable sizing block (zero-request runs,
    pre-1.3 payloads) or nothing needs changing.

    Sizing entries for nodes absent from ``nodes`` are skipped, and any
    malformed entry (non-dict sizing, non-numeric capacity/utilisation)
    degrades to "no suggestion" rather than raising.
    """
    sizing = summary.get("component_sizing")
    if not isinstance(sizing, Mapping) or not sizing:
        return []

    by_name: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        if isinstance(node, Mapping) and isinstance(node.get("name"), str):
            by_name[node["name"]] = node

    suggestions: list[Suggestion] = []
    for name, info in sizing.items():
        if not isinstance(info, Mapping):
            continue
        node = by_name.get(name)
        if node is None:
            continue
        current = node.get("max_capacity")
        if not _is_number(current) or int(current) < _MIN_CAPACITY:
            continue
        current = int(current)

        mean_util = info.get("mean_utilization")
        util_known = _is_number(mean_util)

        status = info.get("status")
        if status == "undersized":
            proposed = max(current + 1, _raise_target(current, mean_util, util_known))
            reason = _raise_reason(name, current, proposed, mean_util, util_known)
        elif status == "oversized":
            proposed = max(_MIN_CAPACITY, int(current * (1.0 - _OVERSIZED_STEP_FRACTION)))
            if proposed >= current:
                continue  # a single slot has nothing to cut
            if util_known and mean_util * current / proposed > TARGET_UTILIZATION:
                continue  # the cut would overload the node: never suggest it
            reason = _cut_reason(name, current, proposed, mean_util, util_known)
        else:  # right_sized (or unknown) → no suggestion
            continue

        suggestions.append(
            Suggestion(
                node=name,
                param="max_capacity",
                current=current,
                proposed=proposed,
                reason=reason,
                est_monthly_delta=_monthly_delta(node.get("cost_per_hour"), current, proposed),
            )
        )

    # Raises first (the risk), then cuts; node name as the tiebreak.
    suggestions.sort(key=lambda s: (0 if s["proposed"] > s["current"] else 1, s["node"]))
    return suggestions


def _raise_target(current: int, mean_util: Any, util_known: bool) -> int:
    """Slots needed for the observed load to sit at the 80% target."""
    if not util_known:
        return current  # max() below still guarantees at least +1 slot
    return math.ceil(current * (mean_util / TARGET_UTILIZATION))


def _raise_reason(
    name: str, current: int, proposed: int, mean_util: Any, util_known: bool
) -> str:
    observed = f"{mean_util * 100:.0f}% mean utilization; " if util_known else ""
    return (
        f"{name} is undersized — {observed}raise max_capacity "
        f"{current} → {proposed} so the load sits at ~80%, then re-run to confirm"
    )


def _cut_reason(
    name: str, current: int, proposed: int, mean_util: Any, util_known: bool
) -> str:
    observed = f"{mean_util * 100:.0f}% mean utilization; " if util_known else ""
    return (
        f"{name} is oversized — {observed}step max_capacity "
        f"{current} → {proposed} (one 25% step), then re-run; repeat until right-sized"
    )


def _monthly_delta(rate: Any, current: int, proposed: int) -> float | None:
    """Provisioned-slots-only monthly price of the change, or ``None``.

    ``cost_per_hour * (proposed - current) * 720`` — metered cost does not
    scale with capacity, so only the slots added/removed are billed.
    ``None`` when the node has no rate (key absent or 0), which also marks
    "no pricing data" to consumers.
    """
    if not _is_number(rate) or rate <= 0:
        return None
    return rate * (proposed - current) * _HOURS_PER_MONTH
