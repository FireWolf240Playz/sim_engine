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
   size that helps as much, and repeat. A raise that keeps the crit count
   and gains :data:`MIN_REPAIR_GAIN` points helps even if it brings a
   warning: a shared bottleneck is cleared one node at a time, and each step
   on the way can turn "breached" into "close to breaching". Stops when
   nothing is left to fix or no raise helps; what remains is reported as
   *not fixable by capacity* (e.g. injected network latency), never papered
   over.
2. **Undersized.** Any node still flagged undersized gets the smallest
   capacity that clears the flag.
3. **Trim.** Over-provisioned nodes are cut to the smallest size that adds
   no crit/warn finding and costs at most :data:`SCORE_TOLERANCE` points
   (anchored to the best score reached).

Phases 2 and 3 repeat until a pass changes nothing. When the passes settle
below the best score reached, the anchor drops to the settled score and they
run again: that is the measure a re-run of the applied config would use, so
it has nothing further to offer. A cut can open room for a raise that did
not help before, so while findings remain and the passes changed something,
repair runs again: the plan ends only where a re-plan would change nothing,
and one apply lands on the end state. Each distinct sizing is simulated once
per plan. Every size is verified *together*: the final config is exactly the
last accepted run, which is also what :class:`FixOutcome` reports (score and
findings before → after), so the UI can show what applying will do.

Deterministic (pinned seed: ``config.seed``, else 42).

**Several seeds** (:func:`plan_fix_seeds`, the page's 5-seed mode): one plan
for the whole run, every candidate simulated on every seed. A finding counts
once per seed it appears on, a node is undersized if it is on any seed, and
the score is the typical run's (:func:`sim_core.profile.typical_index`), the
headline the re-run will show. A cut is free only if no seed gains a finding.
Planning each seed on its own and showing the typical one's plan was the
bug: the typical seed moves after an apply, so no prediction held and
successive plans undid each other.

Cost, measured: the 5-node demo plans in about 0.1 s (15-33 verification
runs, clean run and incidents); the 19-node example topologies take 12-16 s
(170-250 runs), because each pass searches every flagged node again on the
sizes the pass before it settled. Three things keep it from being worse, and
none of them changes a result:

- the run being explained is the plan's "before", never simulated twice;
- a node nobody waited for is not tried for a raise, since more slots would
  replay the same events (``waited_requests`` is counted, not sampled);
- a cut is not simulated above the node's peak slots in use, for the same
  reason (see :func:`_replays_from` for the two exceptions).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, TypedDict

from sim_core.config import ChaosEventType, SimulationConfig
from sim_core.suggestions import TARGET_UTILIZATION, Suggestion, build_suggestions

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
MAX_PASSES = 8

#: Passes → repair rounds after the first repair: a cut can open room for
#: another raise. A round runs only while repair still changes something.
MAX_ROUNDS = 4

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
    ``seeds`` are the seeds every change was verified on (one, or the
    multi-seed run's); ``seed`` is the first of them.
    """

    seed: int
    seeds: list[int]
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
    """crit/warn findings keyed ``id@node`` (per-node ids carry the node).

    A merged multi-seed summary tags each finding with the run it came from
    (``_run``), so the same finding on two seeds counts twice.
    """
    out: dict[str, dict[str, Any]] = {}
    for f in summary.get("findings") or []:
        if isinstance(f, dict) and f.get("severity") in ("crit", "warn"):
            run = f.get("_run")
            prefix = f"{run}:" if run is not None else ""
            out[f"{prefix}{f.get('id')}@{f.get('node') or ''}"] = f
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
    """Fewer crit always helps, more crit never does. At the same crit count
    fewer warn helps, and so does a real score gain even with a warning more:
    under ramp traffic every single raise on the way to clearing a breach
    also brought a p95 headroom warning, and refusing them left a breach that
    capacity fixes reported as unfixable."""
    (crit, warn), (ref_crit, ref_warn) = _counts(candidate), _counts(reference)
    if crit != ref_crit:
        return crit < ref_crit
    if warn < ref_warn:
        return True
    return _score(candidate) >= _score(reference) + MIN_REPAIR_GAIN


def _as_good(candidate: Summary, target: Summary) -> bool:
    return _counts(candidate) <= _counts(target) and _score(candidate) >= (
        _score(target) - SCORE_TOLERANCE
    )


def _cut_is_free(candidate: Summary, reference: Summary, node: str, anchor: float) -> bool:
    """``anchor`` is the best score reached since the passes last settled, so
    the tolerance bounds the cost of a run of cuts together, not each cut
    against the one before."""
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


def _merge(summaries: list[Summary]) -> Summary:
    """One summary standing for a multi-seed run, as the planner reads it.

    The typical run's (the headline's) score, band and everything else; every
    seed's findings, tagged with their run; per node, ``undersized`` if any
    seed says so, the waits of every seed added up, and the highest peak and
    mean utilisation, so a cut is shortcut only when it would replay every
    seed.
    """
    from sim_core.profile import typical_index  # local: keeps imports one-way

    typical = summaries[typical_index(summaries)]
    merged = dict(typical)
    merged["findings"] = [
        {**f, "_run": i}
        for i, s in enumerate(summaries)
        for f in s.get("findings") or []
        if isinstance(f, dict)
    ]
    sizing: dict[str, Any] = {}
    for node, info in (typical.get("component_sizing") or {}).items():
        if not isinstance(info, dict):
            continue
        infos = [
            i for s in summaries
            if isinstance(i := (s.get("component_sizing") or {}).get(node), dict)
        ]
        waited = [i.get("waited_requests") for i in infos]
        peaks = [i.get("peak_in_use") for i in infos]
        utils = [i.get("mean_utilization") for i in infos]
        sizing[node] = {
            **info,
            "status": (
                "undersized" if any(i.get("status") == "undersized" for i in infos)
                else info.get("status")
            ),
            "waited_requests": (
                sum(w for w in waited if isinstance(w, int))
                if all(isinstance(w, int) for w in waited) else None
            ),
            "peak_in_use": (
                max(p for p in peaks if isinstance(p, int))
                if all(isinstance(p, int) for p in peaks) else None
            ),
            "mean_utilization": (
                max(float(u) for u in utils if isinstance(u, (int, float)))
                if all(isinstance(u, (int, float)) for u in utils) else None
            ),
        }
    merged["component_sizing"] = sizing
    return merged


def _seeds_label(seeds: list[int]) -> str:
    if len(seeds) == 1:
        return f"seed {seeds[0]}"
    if seeds == list(range(seeds[0], seeds[0] + len(seeds))):
        return f"seeds {seeds[0]}–{seeds[-1]}"  # noqa: RUF001 — a range, as the page prints it
    return "seeds " + ", ".join(str(s) for s in seeds)


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
    simulate: Simulate | None = None,
) -> FixPlan:
    """Simulation-verified capacity changes for ``config`` and their outcome.

    ``summary`` is the run being explained. When ``config`` carries a seed
    it must be that config's own run on that seed (it is, from
    :meth:`sim_core.engine.CloudSimulator.run`), and it serves as the plan's
    "before" instead of being simulated again. Without a seed the plan pins
    :data:`DEFAULT_SEED` and simulates its own "before". ``simulate`` is
    injectable for tests.
    """
    # Looked up at call time, so a test can replace the module's ``_simulate``.
    simulate_once: Simulate = simulate or _simulate
    seed = config.seed if config.seed is not None else DEFAULT_SEED
    return _plan(config, summary, simulate_once, [seed], shown=config.seed is not None)


def plan_fix_seeds(
    config: SimulationConfig,
    seeds: list[int],
    summaries: list[Summary],
    *,
    simulate: Simulate | None = None,
) -> FixPlan:
    """One plan for a multi-seed run, every change verified on every seed.

    ``summaries[i]`` is ``config``'s own run on ``seeds[i]``
    (``run(suggest=False)``); together they are the plan's "before", never
    simulated again. The outcome's scores are the typical run's, so after
    an apply, the re-run on the same seeds shows ``score_after`` as its
    headline. See the module docstring.
    """
    if not seeds or len(seeds) != len(summaries):
        raise ValueError("plan_fix_seeds needs one summary per seed")
    simulate_once: Simulate = simulate or _simulate

    def simulate_all(candidate: SimulationConfig) -> Summary:
        return _merge([simulate_once(candidate.model_copy(update={"seed": s})) for s in seeds])

    return _plan(
        config.model_copy(update={"seed": seeds[0]}),
        _merge(summaries),
        simulate_all,
        list(seeds),
        shown=True,
    )


def _plan(
    config: SimulationConfig,
    summary: Summary,
    simulate_once: Simulate,
    seeds: list[int],
    *,
    shown: bool,
) -> FixPlan:
    """The plan itself. ``summary`` is the run being explained (merged when
    there are several seeds); ``shown`` says it is ``config``'s own run at
    its sizes, so it is the "before" and not simulated again."""
    nodes = [n.model_dump(mode="json") for n in config.topology.nodes]
    if not build_suggestions(nodes, summary) and not _serious(summary):
        return FixPlan(suggestions=[], outcome=None)

    seen: dict[tuple[int, ...], Summary] = {}

    def run(candidate: SimulationConfig) -> Summary:
        """One run per distinct sizing: within a plan only capacities vary."""
        key = tuple(n.max_capacity for n in candidate.topology.nodes)
        if key not in seen:
            seen[key] = simulate_once(candidate)
        return seen[key]

    working = config.model_copy(update={"seed": seeds[0]})
    if shown:
        seen[tuple(n.max_capacity for n in working.topology.nodes)] = summary
    before = run(working)
    reference = before
    resolved_by: dict[str, list[str]] = {}

    working, reference, _ = _repair(working, reference, run, resolved_by)
    for _ in range(MAX_ROUNDS):
        working, reference, settled = _settle(config, working, reference, run)
        if not settled or not _serious(reference):
            break
        # The cuts may have opened room for a raise that did not help before.
        working, reference, repaired = _repair(working, reference, run, resolved_by)
        if not repaired:
            break  # a re-plan from here would change nothing

    return _finish(config, working, summary, before, reference, resolved_by, seeds)


def _repair(
    working: SimulationConfig,
    reference: Summary,
    run: Simulate,
    resolved_by: dict[str, list[str]],
) -> tuple[SimulationConfig, Summary, bool]:
    """Phase 1 — raise whichever node removes the most failure, repeatedly.
    Also says whether anything changed."""
    repaired = False
    for _ in range(MAX_REPAIR_STEPS):
        if not _serious(reference):
            break
        best: tuple[tuple[int, int, float], str, int, Summary] | None = None
        for n in working.topology.nodes:
            if _waited(reference, n.name) == 0:
                continue  # nobody waited for it: more slots replay the same run
            current = n.max_capacity
            doubled = max(current + 1, current * 2)
            trial = run(_with_capacity(working, n.name, doubled))
            if _helps(trial, reference) and (best is None or _rank(trial) < best[0]):
                best = (_rank(trial), n.name, doubled, trial)
        if best is None:
            break  # no capacity change helps: the cause is not capacity
        _, node, doubled, trial = best
        search = _Search(working, reference, node, run)
        search.runs[doubled] = trial
        current = _capacity(working, node)
        proposed = _smallest(
            current + 1, doubled, lambda c, s=search, t=trial: _as_good(s.at(c), t)
        )
        after = search.at(proposed)
        was = _serious_findings(reference)
        cleared = sorted(set(was) - _serious(after))
        resolved_by.setdefault(node, []).extend(str(was[key].get("id")) for key in cleared)
        working = _with_capacity(working, node, proposed)
        reference = after
        repaired = True
    return working, reference, repaired


def _settle(
    config: SimulationConfig,
    working: SimulationConfig,
    reference: Summary,
    run: Simulate,
) -> tuple[SimulationConfig, Summary, bool]:
    """Phases 2 and 3 — clear undersized flags, then trim, to a fixed point.
    Also says whether anything changed."""
    anchor = _score(reference)
    changed_any = False
    for _ in range(MAX_PASSES):
        changed = False
        flagged = build_suggestions(
            [n.model_dump(mode="json") for n in working.topology.nodes], reference
        )
        for candidate in flagged:  # raises first, then cuts, by name
            node = candidate["node"]
            current = _capacity(working, node)
            search = _Search(working, reference, node, run)
            if candidate["proposed"] > current:
                proposed = _raise_undersized(search, current, reference)
            else:
                replays_from = _replays_from(config, reference, node, current)
                proposed = _smallest_free_cut(search, reference, anchor, current, replays_from)
            if proposed is None or proposed == current:
                continue
            reference = search.at(proposed)
            working = _with_capacity(working, node, proposed)
            anchor = max(anchor, _score(reference))
            changed = changed_any = True
        if changed:
            continue
        if anchor <= _score(reference):
            break
        # Settled, but below the best score reached. A re-run of the applied
        # config measures cuts against its own score, so do the same here;
        # otherwise that re-run would still have a cut to offer.
        anchor = _score(reference)
    return working, reference, changed_any


def _by_finding(summary: Summary) -> dict[str, dict[str, Any]]:
    """crit/warn findings keyed ``id@node`` across every seed: one entry per
    finding, critical if it is critical on any seed."""
    out: dict[str, dict[str, Any]] = {}
    for f in _serious_findings(summary).values():
        key = f"{f.get('id')}@{f.get('node') or ''}"
        if key not in out or f.get("severity") == "crit":
            out[key] = f
    return out


def _finish(
    config: SimulationConfig,
    working: SimulationConfig,
    summary: Summary,
    before: Summary,
    reference: Summary,
    resolved_by: dict[str, list[str]],
    seeds: list[int],
) -> FixPlan:
    """The suggestions and their outcome, from the plan's last accepted run."""
    suggestions = _suggestions(config, working, summary, resolved_by, _seeds_label(seeds))
    if not suggestions and not _serious(before):
        return FixPlan(suggestions=[], outcome=None)

    gone = _by_finding(before)
    remaining = _by_finding(reference)
    rates = {n.name: n.cost_per_hour for n in config.topology.nodes}
    deltas = [s["est_monthly_delta"] for s in suggestions if s["est_monthly_delta"] is not None]
    priced = any(rates.values())
    explanation_before = before.get("score_explanation") or {}
    explanation_after = reference.get("score_explanation") or {}
    outcome = FixOutcome(
        seed=seeds[0],
        seeds=list(seeds),
        score_before=before.get("resilience_score"),
        score_after=reference.get("resilience_score"),
        band_before=explanation_before.get("band"),
        band_after=explanation_after.get("band"),
        resolved=[_ref(f) for key, f in gone.items() if key not in remaining],
        unfixed=[_ref(f) for f in remaining.values()],
        monthly_delta=sum(deltas) if priced and suggestions else None,
    )
    return FixPlan(suggestions=suggestions, outcome=outcome)


def _waited(summary: Summary, node: str) -> int | None:
    """Slot acquisitions on ``node`` that had to queue; ``None`` if unknown."""
    info = (summary.get("component_sizing") or {}).get(node)
    waited = info.get("waited_requests") if isinstance(info, dict) else None
    return waited if isinstance(waited, int) else None


def _replays_from(config: SimulationConfig, reference: Summary, node: str, current: int) -> int:
    """Smallest size of ``node`` known, without simulating, to be a free cut.

    If nobody waited for the node, every size from its peak slots in use
    upward replays the reference run event for event: same score, same
    findings. Two things can still differ, and both are ruled out here. A
    capacity failure scales with the configured size, so a node chaos can
    take slots from is never shortcut. And the node's own utilisation rises
    as it shrinks, so the size must also keep it at or under the target.
    Returns ``current`` when nothing is known: no shortcut.
    """
    info = (reference.get("component_sizing") or {}).get(node)
    if not isinstance(info, dict) or _waited(reference, node) != 0:
        return current
    for event in config.chaos:
        if event.event_type is ChaosEventType.COMPONENT_FAILURE and event.target in (None, node):
            return current
    peak, mean_util = info.get("peak_in_use"), info.get("mean_utilization")
    if not isinstance(peak, int) or not isinstance(mean_util, (int, float)):
        return current
    in_target = math.ceil(mean_util * current / TARGET_UTILIZATION)
    return min(current, max(1, peak, in_target))


def _smallest_free_cut(
    search: _Search, reference: Summary, anchor: float, current: int, replays_from: int
) -> int:
    """Smallest size of the searched node that is still a free cut."""
    node = search.node

    def free(capacity: int) -> bool:
        return _cut_is_free(search.at(capacity), reference, node, anchor)

    if replays_from < current:
        proposed = _smallest(1, current, lambda c: c >= replays_from or free(c))
        if free(proposed):
            return proposed
        # The replay argument did not hold for this run: search every size.
    return _smallest(1, current, free)


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
    verified_on: str,
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
                reason=_reason(
                    n.name, before, after, mean_util, resolved_by.get(n.name), verified_on
                ),
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
    verified_on: str,
) -> str:
    verified = f"(verified by simulation, {verified_on})"
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
    simulate: Simulate | None = None,
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
