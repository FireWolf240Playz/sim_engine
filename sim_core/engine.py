"""CloudSimulator: wires topology, traffic, chaos, and metrics into a runnable
SimPy environment.

This is the orchestrator. It owns the :class:`simpy.Environment` and the live
:class:`sim_core.topology.Topology`, and exposes :meth:`run`. The heavy lifting
is delegated to the sibling modules (config / topology / traffic / chaos /
metrics), so each concern stays in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Generator, List, Optional

import numpy as np
import simpy

from sim_core import chaos as chaos_mod
from sim_core.config import ChaosEvent, SimulationConfig
from sim_core.metrics import MetricsCollector, RequestRecord
from sim_core.traffic import generate_traffic
from sim_core.topology import Component, Topology

if TYPE_CHECKING:
    from simpy.resources.resource import Request


class ComponentTimeoutError(RuntimeError):
    """A component slot could not be acquired within the timeout budget.

    Raised after ``attempts`` *real* acquisition attempts, each bounded by a
    genuine ``env.timeout()`` wait (with a real ``env.timeout()`` backoff
    between attempts). The request that hit this error is recorded as failed
    - this is a live simulation outcome, not a post-hoc annotation.
    """

    def __init__(self, component_name: str, attempts: int) -> None:
        self.component_name = component_name
        self.attempts = attempts
        super().__init__(
            f"component {component_name!r} could not be acquired within the "
            f"timeout budget ({attempts} attempt(s))"
        )


class CloudSimulator:
    """Runs a pre-deployment cloud-resilience simulation."""

    def __init__(self, config: SimulationConfig) -> None:
        self.config = config
        self.env = simpy.Environment()
        # Two independent RNG streams from one seed: arrivals get their own
        # stream so the arrival pattern is identical across scenarios - only
        # what chaos/service behavior does differs between runs. (A single
        # shared stream would let scenario behavior shift *which* random values
        # the arrival process consumes, confounding any A/B comparison.)
        self._arrival_rng, self.rng = np.random.default_rng(config.seed).spawn(2)
        self.topology = Topology(self.env, config.topology)
        self.collector = MetricsCollector(self.topology)
        self._req_counter = 0

    # -- request serving ---------------------------------------------------
    def serve_request(self, request_id: str) -> Generator[object, None, None]:
        """Process one request by walking the request-path graph from the entry node.

        Every node is served through its live SimPy resource (slot acquire,
        sampled service time, release). A node's outgoing edges are each
        followed *independently* with their configured probability, so a node
        can fan out to several downstream components (e.g. a worker calling
        two services), each visited with its own probability. A ``cache``-role
        node decides hit/miss from its *live* hit-rate (so ``cache_outage``
        chaos still steers real traffic): a hit serves the response locally
        and the branch ends; a miss continues along the cache's outgoing
        edges. This generalises the legacy lb -> worker -> cache -> (miss)
        -> db pipeline, which is the same graph with all edges at p=1.0.

        If a component's slot cannot be acquired within its configured timeout
        (after all configured retries), or the request is interrupted
        mid-flight, it is recorded as failed.
        """
        start = self.env.now
        component_times: Dict[str, float] = {}
        success = False
        # Single-element box so the (recursive) walk can report back the first
        # cache hit/miss it sees without changing the generator's yield types.
        cache_hit: List[Optional[bool]] = [None]

        try:
            yield from self._walk(self.topology.entry_node, component_times, cache_hit, frozenset())
            success = True
        except simpy.Interrupt:
            # The request was aborted mid-flight (e.g. a hard chaos failure);
            # record it as a completion failure.
            success = False
        except ComponentTimeoutError:
            # All acquisition attempts for some component timed out; record
            # the request as a completion failure.
            success = False

        end = self.env.now
        latency = end - start
        sla_met = (
            True if self.config.sla_target is None else latency <= self.config.sla_target
        )
        self.collector.record_request(
            RequestRecord(
                request_id=request_id,
                start=start,
                end=end,
                success=success,
                sla_met=bool(sla_met),
                cache_hit=cache_hit[0],
                component_times=component_times,
            )
        )

    def _walk(
        self,
        node_name: str,
        component_times: Dict[str, float],
        cache_hit: List[Optional[bool]],
        on_path: frozenset,
    ) -> Generator[object, None, None]:
        """Serve one node, then follow the outgoing edges this request takes.

        ``on_path`` is the ancestor chain of the current walk; a back-edge to
        an ancestor would recurse forever. Topology construction already
        rejects cycles, so this is only reachable if the live graph changes
        under us - fail loudly instead of hanging.
        """
        node = self.topology.component(node_name)
        yield from self._use_component(node, component_times)

        # Read-through cache semantics (live hit-rate, mutated by chaos):
        # a hit serves the response and ends this branch; a miss falls
        # through to the downstream edges.
        if node.is_cache:
            if cache_hit[0] is None:
                cache_hit[0] = bool(self.rng.random() < node.hit_rate)
            if cache_hit[0]:
                return

        for edge in self.topology.outgoing_edges(node_name):
            if edge.target in on_path:
                raise ValueError(
                    f"cycle detected in the request path (re-entering node "
                    f"{edge.target!r}) - the graph must be a DAG"
                )
            if self.rng.random() >= edge.probability:
                continue  # this branch is not taken for this request
            yield from self._walk(edge.target, component_times, cache_hit, on_path | {node_name})

    def _acquire_slot(
        self, component: Component, timeout: Optional[float]
    ) -> Generator[object, None, Optional["Request"]]:
        """Wait for a slot on *component*, bounded by *timeout*.

        Returns the granted :class:`Request` (the caller must release it, e.g.
        via ``with req:``), or ``None`` when the timeout expired first. A
        request that loses the race is cancelled so it never lingers in the
        resource queue, and an interrupt while waiting cancels it the same way.
        """
        req = component.resource.request()
        try:
            if timeout is None:
                yield req
            else:
                yield req | self.env.timeout(timeout)
        except BaseException:
            # Interrupted while waiting: drop the pending request, re-raise.
            req.cancel()
            raise
        if req.triggered:
            return req
        req.cancel()
        return None

    def _use_component(
        self, component: Component, component_times: Dict[str, float]
    ) -> Generator[object, None, None]:
        """Acquire a slot (honouring timeout + retries), service, release.

        Every acquisition attempt is *real*: a genuine ``resource.request()``
        racing a genuine ``env.timeout(timeout)``, with a real
        ``env.timeout(retry_backoff)`` between attempts. When the slot is
        granted, the ``with`` block services the request for a sampled duration
        and auto-releases the slot on exit (interrupt-safe, verified against
        SimPy's ``Request.__exit__``). If all ``retry_limit + 1`` attempts
        time out, :class:`ComponentTimeoutError` is raised and the caller
        records the request as failed.
        """
        cfg = component.config
        max_attempts = cfg.retry_limit + 1
        for attempt in range(max_attempts):
            req = yield from self._acquire_slot(component, cfg.timeout)
            if req is not None:
                t0 = self.env.now
                with req:
                    yield self.env.timeout(component.draw_service_time(self.rng))
                component_times[component.name] = self.env.now - t0
                return
            # This attempt timed out; retry after a real backoff delay.
            if attempt + 1 < max_attempts:
                self.collector.record_retry(component.name)
                yield self.env.timeout(cfg.retry_backoff)
        raise ComponentTimeoutError(component.name, max_attempts)

    # -- periodic metric sampling -----------------------------------------
    def collect_utilization(self) -> Generator[object, None, None]:
        while True:
            self.collector.sample_utilization(self.env.now)
            yield self.env.timeout(self.config.metrics_interval)

    # -- orchestration -----------------------------------------------------
    def _spawn_processes(self) -> None:
        """Schedule the traffic generator, every chaos event, and metric sampler."""
        env = self.env
        env.process(generate_traffic(env, self.config.traffic, self.serve_request, self._arrival_rng))
        for event in self.config.chaos:
            env.process(chaos_mod.inject(env, self.topology, event, self.rng))
        env.process(self.collect_utilization())

    def run(self) -> Dict[str, Any]:
        """Run to the configured duration and return the headline summary.

        ``env.run(until=...)`` bounds the run: the traffic generator, chaos
        loops, and metric sampler all contain ``while True``/bounded loops that
        are simply suspended once the clock reaches the horizon.
        """
        self._spawn_processes()
        self.env.run(until=self.config.duration)
        self.collector.settle_cost(self.config.duration)
        return self.collector.summary()

    # -- convenience -------------------------------------------------------
    @property
    def request_ids(self) -> List[str]:
        return [r.request_id for r in self.collector.requests]
