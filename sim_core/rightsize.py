"""Verified right-sizing — "fix it" sizes proven by simulation (roadmap 1.3d).

:func:`sim_core.suggestions.build_suggestions` is a pure formula over one
run's utilisation: it says *which* nodes to change and in *which direction*,
but a formula cannot say how far is safe. Latency is non-linear near
saturation and depends on the whole request chain, so a utilisation-only cut
can breach the SLA (the demo's worker 6 → 4 did exactly that).

:func:`right_size` keeps the formula as the flagging step and then *proves*
each size by re-running the engine on the same seed:

- **undersized** → the smallest capacity above the current one at which the
  node is no longer undersized (doubling, then bisecting).
- **oversized** → the smallest capacity at or below the current one that
  costs nothing: the node does not become undersized, no crit/warn finding
  appears that the reference run did not already have, and the resilience
  score drops by at most :data:`SCORE_TOLERANCE`.

Nodes are sized one after another, raises first, each on the config with the
earlier changes already applied, so the final set is verified *together*:
applying every suggestion at once is exactly the last accepted run. One
apply lands on the right size; there is no 25% staircase.

Deterministic: candidate runs pin the seed (``config.seed``, or 42 when the
config has none), so the same config always yields the same list. A run is
a few milliseconds, so the search (a handful of runs per flagged node) adds
well under a second even on multi-seed runs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sim_core.config import SimulationConfig
from sim_core.suggestions import Suggestion, build_suggestions

#: How many resilience points a cut may cost and still count as free. Same
#: seed, but a different capacity reorders events, so tiny jitter is noise.
SCORE_TOLERANCE = 0.5

#: Upper bound on how far a raise may search (times current), so a node that no
#: capacity can fix (e.g. an upstream bottleneck) ends the search quickly.
RAISE_SEARCH_FACTOR = 16

#: Sizing passes before giving up on reaching a fixed point (each pass that
#: changes something re-flags the nodes from the newly right-sized run).
MAX_PASSES = 3

#: Seed used for verification when the config has none.
DEFAULT_SEED = 42

_HOURS_PER_MONTH = 720

Summary = dict[str, Any]
Simulate = Callable[[SimulationConfig], Summary]


def _simulate(config: SimulationConfig) -> Summary:
    from sim_core.engine import CloudSimulator  # local: engine imports this module lazily

    return CloudSimulator(config).run(suggest=False)


def _with_capacity(config: SimulationConfig, node: str, capacity: int) -> SimulationConfig:
    nodes = [
        n.model_copy(update={"max_capacity": capacity}) if n.name == node else n
        for n in config.topology.nodes
    ]
    topology = config.topology.model_copy(update={"nodes": nodes})
    return config.model_copy(update={"topology": topology})


def _capacity(config: SimulationConfig, node: str) -> int:
    for n in config.topology.nodes:
        if n.name == node:
            return n.max_capacity
    raise KeyError(node)


def _status(summary: Summary, node: str) -> str | None:
    info = (summary.get("component_sizing") or {}).get(node)
    return info.get("status") if isinstance(info, dict) else None


def _serious(summary: Summary) -> set[str]:
    """Ids of the crit/warn findings in a run (per-node ids carry the node)."""
    out: set[str] = set()
    for f in summary.get("findings") or []:
        if f.get("severity") in ("crit", "warn"):
            out.add(f"{f.get('id')}:{f.get('node') or ''}")
    return out


def _score(summary: Summary) -> float:
    score = summary.get("resilience_score")
    return float(score) if score is not None else 0.0


def _cut_is_free(candidate: Summary, reference: Summary, node: str, anchor: float) -> bool:
    """``anchor`` is the best score reached so far, so the tolerance bounds
    the *total* cost of every cut, not each cut against the one before."""
    return (
        _status(candidate, node) != "undersized"
        and _serious(candidate) <= _serious(reference)
        and _score(candidate) >= anchor - SCORE_TOLERANCE
    )


def _raise_clears(candidate: Summary, node: str) -> bool:
    return _status(candidate, node) != "undersized"


def _smallest(lo: int, hi: int, ok: Callable[[int], bool]) -> int:
    """Smallest value in ``[lo, hi]`` with ``ok(value)``, given ``ok(hi)``.

    Bisection assumes ``ok`` is monotone in capacity. It is close to that,
    not exactly; the returned value itself is always one that was verified.
    """
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    return hi


def _reason(node: str, before: int, after: int, mean_util: float | None, seed: int) -> str:
    observed = f"{mean_util * 100:.0f}% mean utilization; " if mean_util is not None else ""
    if after > before:
        what = f"raise max_capacity {before} → {after}, the smallest size that clears it"
        state = "undersized"
    else:
        what = (
            f"cut max_capacity {before} → {after}, the smallest size that keeps "
            "the SLA and adds no finding"
        )
        state = "oversized"
    return f"{node} is {state} — {observed}{what} (verified by simulation, seed {seed})"


def right_size(
    config: SimulationConfig,
    summary: Summary,
    *,
    simulate: Simulate = _simulate,
) -> list[Suggestion]:
    """Simulation-verified capacity changes for ``config``.

    ``summary`` is the run being explained; it only decides which nodes are
    flagged (via :func:`build_suggestions`). Every proposed size is proven
    by ``simulate`` on the pinned seed. ``simulate`` is injectable for tests.
    """
    nodes = [n.model_dump(mode="json") for n in config.topology.nodes]
    flagged = build_suggestions(nodes, summary)
    if not flagged:
        return []

    seed = config.seed if config.seed is not None else DEFAULT_SEED
    working = config.model_copy(update={"seed": seed})
    reference = simulate(working)
    anchor = _score(reference)

    # Passes repeat until one changes nothing: a size that was not free
    # next to the original neighbours can be free once they are right-sized,
    # and the user should get the end state from a single apply.
    for _ in range(MAX_PASSES):
        changed = False
        for candidate in flagged:  # raises first, then cuts, by name
            node = candidate["node"]
            current = _capacity(working, node)
            upward = candidate["proposed"] > current
            proposed = _search(working, reference, node, upward, anchor, simulate)
            if proposed is None or proposed == current:
                continue
            working = _with_capacity(working, node, proposed)
            reference = simulate(working)
            anchor = max(anchor, _score(reference))
            changed = True
        if not changed:
            break
        nodes = [n.model_dump(mode="json") for n in working.topology.nodes]
        flagged = build_suggestions(nodes, reference)

    sizing = summary.get("component_sizing") or {}
    accepted: list[Suggestion] = []
    for original in config.topology.nodes:
        node = original.name
        before, after = original.max_capacity, _capacity(working, node)
        if before == after:
            continue
        info = sizing.get(node) if isinstance(sizing, dict) else None
        mean_util = info.get("mean_utilization") if isinstance(info, dict) else None
        rate = original.cost_per_hour
        accepted.append(
            Suggestion(
                node=node,
                param="max_capacity",
                current=before,
                proposed=after,
                reason=_reason(node, before, after, mean_util, seed),
                est_monthly_delta=rate * (after - before) * _HOURS_PER_MONTH if rate > 0 else None,
            )
        )
    accepted.sort(key=lambda s: (0 if s["proposed"] > s["current"] else 1, s["node"]))
    return accepted


def _search(
    working: SimulationConfig,
    reference: Summary,
    node: str,
    upward: bool,
    anchor: float,
    simulate: Simulate,
) -> int | None:
    """The verified capacity for one node, or ``None`` when none is found."""
    current = _capacity(working, node)
    runs: dict[int, Summary] = {current: reference}

    def run_at(capacity: int) -> Summary:
        if capacity not in runs:
            runs[capacity] = simulate(_with_capacity(working, node, capacity))
        return runs[capacity]

    if upward:
        if _raise_clears(reference, node):
            return None  # the earlier changes already cleared it
        limit = current * RAISE_SEARCH_FACTOR
        lo, hi = current + 1, current * 2
        while not _raise_clears(run_at(hi), node):
            if hi >= limit:
                return None  # no capacity clears it: the bottleneck is elsewhere
            lo, hi = hi + 1, min(hi * 2, limit)
        return _smallest(lo, hi, lambda c: _raise_clears(run_at(c), node))
    return _smallest(1, current, lambda c: _cut_is_free(run_at(c), reference, node, anchor))
