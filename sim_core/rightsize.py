"""Verified right-sizing — "fix it" sizes proven by simulation (roadmap 1.3d).

:func:`sim_core.suggestions.build_suggestions` is a pure formula over one
run's utilisation. It can say which nodes look over- or undersized, but not
how far is safe, and it is blind to the failures an incident causes: under
``db_failover`` every node can read "oversized" on average while the run
breaches its SLA. A formula-only "fix" then offers nothing but cuts.

:func:`plan_fix` proves every change by re-running the engine on the same
pinned seed, in three phases on one working config:

1. **Repair.** While the run has crit/warn findings, try doubling each
   node's capacity, keep the node whose raise helps most (fewest crit, then
   fewest warn, then highest score), trim that raise back to the smallest
   size that helps as much, and repeat. Stops when nothing is left to fix or
   no raise helps; what remains is reported as *not fixable by capacity*
   (e.g. injected network latency), never papered over.
2. **Undersized.** Any node still flagged undersized gets the smallest
   capacity that clears the flag.
3. **Trim.** Over-provisioned nodes are cut to the smallest size that adds
   no crit/warn finding and costs at most :data:`SCORE_TOLERANCE` points in
   total (anchored to the best score reached).

Phases 2 and 3 repeat until a pass changes nothing, so one apply lands on the
end state. Every size is verified *together*: the final config is exactly
the last accepted run, which is also what :class:`FixOutcome` reports (score
and findings before → after), so the UI can show what applying will do.

Deterministic (pinned seed: ``config.seed``, else 42). A run is a few ms, so
the whole search is typically well under half a second.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from sim_core.config import SimulationConfig
from sim_core.suggestions import Suggestion, build_suggestions

#: How many resilience points a cut may cost and still count as free. Same
#: seed, but a different capacity reorders events, so tiny jitter is noise.
SCORE_TOLERANCE = 0.5

#: A repair raise that clears no finding must gain at least this many points.
MIN_REPAIR_GAIN = 1.0

#: Repair rounds (each picks one node to raise) before giving up.
MAX_REPAIR_STEPS = 8

#: Upper bound on how far an undersized raise may search (times current).
RAISE_SEARCH_FACTOR = 16

#: Undersized/trim passes before giving up on reaching a fixed point.
MAX_PASSES = 3

#: Seed used for verification when the config has none.
DEFAULT_SEED = 42

_HOURS_PER_MONTH = 720

Summary = dict[str, Any]
Simulate = Callable[[SimulationConfig], Summary]


class FindingRef(TypedDict):
    """A finding, reduced to what a before → after comparison needs."""

    id: str
    node: str | None
    severity: str
    title: str


class FixOutcome(TypedDict):
    """What applying every suggestion does, from the verification run itself.

    ``before`` is the pinned-seed run of the config as given, ``after`` the
    run with every suggestion applied. ``unfixed`` lists the crit/warn
    findings no capacity change cleared: their cause is not capacity.
    """

    seed: int
    score_before: float | None
    score_after: float | None
    band_before: str | None
    band_after: str | None
    resolved: list[FindingRef]
    unfixed: list[FindingRef]
    monthly_delta: float | None


class FixPlan(TypedDict):
    suggestions: list[Suggestion]
    outcome: FixOutcome | None


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


def _serious_findings(summary: Summary) -> dict[str, dict[str, Any]]:
    """crit/warn findings keyed ``id@node`` (per-node ids carry the node)."""
    out: dict[str, dict[str, Any]] = {}
    for f in summary.get("findings") or []:
        if isinstance(f, dict) and f.get("severity") in ("crit", "warn"):
            out[f"{f.get('id')}@{f.get('node') or ''}"] = f
    return out


def _serious(summary: Summary) -> set[str]:
    return set(_serious_findings(summary))


def _counts(summary: Summary) -> tuple[int, int]:
    found = _serious_findings(summary).values()
    return (
        sum(1 for f in found if f.get("severity") == "crit"),
        sum(1 for f in found if f.get("severity") == "warn"),
    )


def _score(summary: Summary) -> float:
    score = summary.get("resilience_score")
    return float(score) if score is not None else 0.0


def _rank(summary: Summary) -> tuple[int, int, float]:
    """Lower is better: fewest crit, then fewest warn, then highest score."""
    crit, warn = _counts(summary)
    return (crit, warn, -_score(summary))


def _helps(candidate: Summary, reference: Summary) -> bool:
    if _counts(candidate) < _counts(reference):
        return True
    return (
        _counts(candidate) == _counts(reference)
        and _score(candidate) >= _score(reference) + MIN_REPAIR_GAIN
    )


def _as_good(candidate: Summary, target: Summary) -> bool:
    return _counts(candidate) <= _counts(target) and _score(candidate) >= (
        _score(target) - SCORE_TOLERANCE
    )


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


class _Search:
    """Memoised runs of one working config with one node's capacity varied."""

    def __init__(self, working: SimulationConfig, reference: Summary, node: str,
                 simulate: Simulate) -> None:
        self.working = working
        self.node = node
        self.simulate = simulate
        self.runs: dict[int, Summary] = {_capacity(working, node): reference}

    def at(self, capacity: int) -> Summary:
        if capacity not in self.runs:
            self.runs[capacity] = self.simulate(_with_capacity(self.working, self.node, capacity))
        return self.runs[capacity]


def _ref(f: dict[str, Any]) -> FindingRef:
    node = f.get("node")
    return FindingRef(
        id=str(f.get("id")),
        node=str(node) if node else None,
        severity=str(f.get("severity")),
        title=str(f.get("title") or f.get("text") or f.get("id")),
    )


def plan_fix(
    config: SimulationConfig,
    summary: Summary,
    *,
    simulate: Simulate = _simulate,
) -> FixPlan:
    """Simulation-verified capacity changes for ``config`` and their outcome.

    ``summary`` is the run being explained; it is used only to skip work
    when nothing is flagged. ``simulate`` is injectable for tests.
    """
    nodes = [n.model_dump(mode="json") for n in config.topology.nodes]
    if not build_suggestions(nodes, summary) and not _serious(summary):
        return FixPlan(suggestions=[], outcome=None)

    seed = config.seed if config.seed is not None else DEFAULT_SEED
    working = config.model_copy(update={"seed": seed})
    before = simulate(working)
    reference = before
    resolved_by: dict[str, list[str]] = {}

    # Phase 1 — repair: raise whichever node removes the most failure.
    for _ in range(MAX_REPAIR_STEPS):
        if not _serious(reference):
            break
        best: tuple[tuple[int, int, float], str, int, Summary] | None = None
        for n in working.topology.nodes:
            current = n.max_capacity
            doubled = max(current + 1, current * 2)
            trial = simulate(_with_capacity(working, n.name, doubled))
            if _helps(trial, reference) and (best is None or _rank(trial) < best[0]):
                best = (_rank(trial), n.name, doubled, trial)
        if best is None:
            break  # no capacity change helps: the cause is not capacity
        _, node, doubled, trial = best
        search = _Search(working, reference, node, simulate)
        search.runs[doubled] = trial
        current = _capacity(working, node)
        proposed = _smallest(
            current + 1, doubled, lambda c, s=search, t=trial: _as_good(s.at(c), t)
        )
        after = search.at(proposed)
        cleared = sorted(_serious(reference) - _serious(after))
        resolved_by.setdefault(node, []).extend(key.split("@")[0] for key in cleared)
        working = _with_capacity(working, node, proposed)
        reference = after

    # Phases 2 and 3: clear undersized flags, then trim, until a fixed point.
    anchor = _score(reference)
    for _ in range(MAX_PASSES):
        changed = False
        flagged = build_suggestions(
            [n.model_dump(mode="json") for n in working.topology.nodes], reference
        )
        for candidate in flagged:  # raises first, then cuts, by name
            node = candidate["node"]
            current = _capacity(working, node)
            search = _Search(working, reference, node, simulate)
            if candidate["proposed"] > current:
                proposed = _raise_undersized(search, current, reference)
            else:
                proposed = _smallest(
                    1,
                    current,
                    lambda c, s=search, r=reference, n=node, a=anchor: _cut_is_free(
                        s.at(c), r, n, a
                    ),
                )
            if proposed is None or proposed == current:
                continue
            reference = search.at(proposed)
            working = _with_capacity(working, node, proposed)
            anchor = max(anchor, _score(reference))
            changed = True
        if not changed:
            break

    suggestions = _suggestions(config, working, summary, resolved_by, seed)
    if not suggestions and not _serious(before):
        return FixPlan(suggestions=[], outcome=None)

    gone = _serious_findings(before)
    remaining = _serious_findings(reference)
    rates = {n.name: n.cost_per_hour for n in config.topology.nodes}
    deltas = [s["est_monthly_delta"] for s in suggestions if s["est_monthly_delta"] is not None]
    priced = any(rates.values())
    explanation_before = before.get("score_explanation") or {}
    explanation_after = reference.get("score_explanation") or {}
    outcome = FixOutcome(
        seed=seed,
        score_before=before.get("resilience_score"),
        score_after=reference.get("resilience_score"),
        band_before=explanation_before.get("band"),
        band_after=explanation_after.get("band"),
        resolved=[_ref(f) for key, f in gone.items() if key not in remaining],
        unfixed=[_ref(f) for f in remaining.values()],
        monthly_delta=sum(deltas) if priced and suggestions else None,
    )
    return FixPlan(suggestions=suggestions, outcome=outcome)


def _raise_undersized(search: _Search, current: int, reference: Summary) -> int | None:
    node = search.node
    if _raise_clears(reference, node):
        return None
    limit = current * RAISE_SEARCH_FACTOR
    lo, hi = current + 1, current * 2
    while not _raise_clears(search.at(hi), node):
        if hi >= limit:
            return None  # no capacity clears it: the bottleneck is elsewhere
        lo, hi = hi + 1, min(hi * 2, limit)
    return _smallest(lo, hi, lambda c: _raise_clears(search.at(c), node))


def _suggestions(
    original: SimulationConfig,
    working: SimulationConfig,
    summary: Summary,
    resolved_by: dict[str, list[str]],
    seed: int,
) -> list[Suggestion]:
    sizing = summary.get("component_sizing") or {}
    out: list[Suggestion] = []
    for n in original.topology.nodes:
        before, after = n.max_capacity, _capacity(working, n.name)
        if before == after:
            continue
        info = sizing.get(n.name) if isinstance(sizing, dict) else None
        mean_util = info.get("mean_utilization") if isinstance(info, dict) else None
        out.append(
            Suggestion(
                node=n.name,
                param="max_capacity",
                current=before,
                proposed=after,
                reason=_reason(n.name, before, after, mean_util, resolved_by.get(n.name), seed),
                est_monthly_delta=(
                    n.cost_per_hour * (after - before) * _HOURS_PER_MONTH
                    if n.cost_per_hour > 0
                    else None
                ),
            )
        )
    out.sort(key=lambda s: (0 if s["proposed"] > s["current"] else 1, s["node"]))
    return out


def _reason(
    node: str,
    before: int,
    after: int,
    mean_util: float | None,
    cleared: list[str] | None,
    seed: int,
) -> str:
    verified = f"(verified by simulation, seed {seed})"
    if after > before and cleared:
        names = ", ".join(dict.fromkeys(c.replace("_", " ") for c in cleared))
        return f"{node}: raise max_capacity {before} → {after} — clears {names} {verified}"
    if after > before:
        return (
            f"{node}: raise max_capacity {before} → {after} — the smallest size that "
            f"removes its bottleneck {verified}"
        )
    observed = f" at {mean_util * 100:.0f}% mean utilization" if mean_util is not None else ""
    return (
        f"{node} is oversized{observed}: cut max_capacity {before} → {after}, the "
        f"smallest size that keeps the SLA and adds no finding {verified}"
    )


def right_size(
    config: SimulationConfig,
    summary: Summary,
    *,
    simulate: Simulate = _simulate,
) -> list[Suggestion]:
    """Just the suggestion list of :func:`plan_fix`."""
    return plan_fix(config, summary, simulate=simulate)["suggestions"]


def verified_sizing(
    config: SimulationConfig,
    suggestions: list[Suggestion],
) -> dict[str, tuple[str, int]]:
    """Per-node sizing verdict as the verified plan applies it, never a guess.

    For each node (in ``config.topology.nodes`` order) the ``after`` size is
    the plan's proposal for that node when there is one, else its current
    ``max_capacity``, and the status names that move — ``oversized`` when the
    plan cuts, ``undersized`` when it raises, ``right_sized`` when it leaves
    the node alone. Pure: it reads the plan, it never simulates, so the label
    on screen can never disagree with what "Fix it" would actually do.
    """
    proposed = {s["node"]: s["proposed"] for s in suggestions}
    out: dict[str, tuple[str, int]] = {}
    for n in config.topology.nodes:
        after = proposed.get(n.name, n.max_capacity)
        status = (
            "oversized" if after < n.max_capacity
            else "undersized" if after > n.max_capacity
            else "right_sized"
        )
        out[n.name] = (status, after)
    return out
