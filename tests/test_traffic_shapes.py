"""Contract for roadmap 3.5, engine half: traffic shaped like a real day
(`.agents/tasks/3.5-traffic-shapes.md`).

``TrafficPattern.shape`` bends the base rate over the traffic window:

- ``flat``          the rate as configured (the default; nothing changes);
- ``ramp``          climbs in a straight line from ``base_rps`` to the peak;
- ``diurnal``       one day: quiet at both ends, the peak in the middle;
- ``black_friday``  a normal day, then a surge: flat for 40% of the window,
                    a climb over the next 10%, the peak held for 30%, and a
                    decline over the last 20%.

``base_rps`` is always the quiet rate; ``shape_peak`` is how many times
busier the peak is (default 3 for ``ramp`` and ``diurnal``, 5 for
``black_friday``). With ``u`` the fraction of the window elapsed and ``P``
the peak, the rate is ``base_rps`` times:

    ramp          1 + (P - 1) u
    diurnal       1 + (P - 1) sin^2(pi u)
    black_friday  1                          u < 0.4
                  1 + (P - 1)(u - 0.4)/0.1   0.4 <= u < 0.5
                  P                          0.5 <= u < 0.8
                  P - (P - 1)(u - 0.8)/0.2   0.8 <= u

The same numbers are pinned in ``web/tests/trafficShape.test.ts``: the
picker's preview draws this curve.

A flat pattern draws exactly what it always drew
(``test_traffic.py::test_a_constant_rate_keeps_its_exact_arrival_times``).

Make these pass. Do not edit them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Generator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import simpy
from pydantic import ValidationError

from sim_core import CloudSimulator, SimulationConfig
from sim_core.cli import main
from sim_core.cli.sim import default_config
from sim_core.config import DEFAULT_SHAPE_PEAK, MAX_EXPECTED_REQUESTS, TrafficPattern
from sim_core.presets import TRAFFIC_PRESETS, traffic_preset
from sim_core.traffic import generate_traffic

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SHAPES = ("flat", "ramp", "diurnal", "black_friday")


def _pattern(shape: str, **overrides: Any) -> TrafficPattern:
    fields: dict[str, Any] = dict(base_rps=2.0, duration=100.0, shape=shape)
    fields.update(overrides)
    return TrafficPattern(**fields)


def _arrivals(pattern: TrafficPattern, seed: int) -> list[float]:
    env = simpy.Environment()
    times: list[float] = []

    def request(_request_id: str) -> Generator[Any]:
        times.append(env.now)
        yield env.timeout(0)

    env.process(generate_traffic(env, pattern, request, np.random.default_rng(seed)))
    env.run(until=pattern.start_time + pattern.duration + 1.0)
    return times


def _integral(pattern: TrafficPattern, lo: float, hi: float, steps: int = 20_000) -> float:
    """Midpoint rule over ``rate_at``: what the profile adds up to, by brute force."""
    width = (hi - lo) / steps
    return sum(pattern.rate_at(lo + (i + 0.5) * width) for i in range(steps)) * width


# ---------------------------------------------------------------------------
# the rate schedule
# ---------------------------------------------------------------------------


def test_flat_is_the_default_and_is_the_base_rate() -> None:
    pattern = TrafficPattern(base_rps=2.0, duration=100.0)
    assert pattern.shape == "flat" and pattern.shape_peak is None
    assert [pattern.rate_at(t) for t in (0.0, 37.0, 99.9)] == [2.0, 2.0, 2.0]


def test_default_peaks() -> None:
    assert DEFAULT_SHAPE_PEAK == {"ramp": 3.0, "diurnal": 3.0, "black_friday": 5.0}


@pytest.mark.parametrize(
    ("shape", "t", "rate"),
    [
        ("ramp", 0.0, 2.0),
        ("ramp", 25.0, 3.0),
        ("ramp", 50.0, 4.0),
        ("ramp", 75.0, 5.0),
        ("diurnal", 0.0, 2.0),
        ("diurnal", 25.0, 4.0),
        ("diurnal", 50.0, 6.0),
        ("diurnal", 75.0, 4.0),
        ("black_friday", 0.0, 2.0),
        ("black_friday", 39.0, 2.0),
        ("black_friday", 45.0, 6.0),
        ("black_friday", 50.0, 10.0),
        ("black_friday", 79.0, 10.0),
        ("black_friday", 90.0, 6.0),
    ],
)
def test_the_rate_at_a_moment(shape: str, t: float, rate: float) -> None:
    """base 2 rps over 100 s, default peak. Pinned in the web suite too."""
    assert _pattern(shape).rate_at(t) == pytest.approx(rate, abs=1e-9)


def test_a_chosen_peak() -> None:
    assert _pattern("ramp", shape_peak=10.0).rate_at(50.0) == pytest.approx(11.0)
    assert _pattern("black_friday", shape_peak=2.0).rate_at(60.0) == pytest.approx(4.0)


def test_the_curve_follows_the_window_not_the_clock() -> None:
    """A window that opens at t=40 starts its curve there."""
    pattern = _pattern("ramp", start_time=40.0)
    assert pattern.rate_at(40.0) == pytest.approx(2.0)
    assert pattern.rate_at(90.0) == pytest.approx(4.0)


def test_no_traffic_outside_the_window() -> None:
    pattern = _pattern("diurnal", start_time=10.0)
    assert pattern.rate_at(9.99) == 0.0
    assert pattern.rate_at(110.0) == 0.0
    assert pattern.rate_at(500.0) == 0.0


def test_a_spike_still_overrides_the_curve() -> None:
    pattern = _pattern("ramp", spike_rps=50.0, spike_start=20.0, spike_duration=10.0)
    assert pattern.rate_at(25.0) == 50.0
    assert pattern.rate_at(19.0) == pytest.approx(2.0 * (1 + 2 * 0.19))
    assert pattern.rate_at(30.0) == pytest.approx(2.0 * (1 + 2 * 0.30))


def test_every_shape_draws_its_own_curve() -> None:
    curves = {
        shape: tuple(round(_pattern(shape).rate_at(t), 6) for t in range(0, 100, 5))
        for shape in SHAPES
    }
    assert len(set(curves.values())) == 4
    assert all(min(curve) >= 2.0 for curve in curves.values())  # base_rps is the quiet rate


def test_exponential_growth_keeps_its_rate() -> None:
    pattern = TrafficPattern(
        base_rps=2.0, duration=100.0, pattern_type="exponential", growth_period=50.0
    )
    assert pattern.rate_at(50.0) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# what a profile adds up to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "expected"),
    [("flat", 200.0), ("ramp", 400.0), ("diurnal", 400.0), ("black_friday", 560.0)],
)
def test_expected_requests_over_the_whole_window(shape: str, expected: float) -> None:
    assert _pattern(shape).expected_requests(100.0) == pytest.approx(expected, rel=1e-9)


def test_expected_requests_is_cut_at_the_horizon() -> None:
    assert _pattern("ramp").expected_requests(50.0) == pytest.approx(150.0, rel=1e-9)
    assert _pattern("black_friday").expected_requests(40.0) == pytest.approx(80.0, rel=1e-9)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("horizon", [33.0, 77.0, 140.0, 500.0])
def test_expected_requests_is_the_integral_of_the_rate(shape: str, horizon: float) -> None:
    """With an offset window and a spike across a bend of the curve."""
    pattern = _pattern(
        shape,
        start_time=20.0,
        duration=110.0,
        spike_rps=30.0,
        spike_start=60.0,
        spike_duration=12.0,
    )
    brute = _integral(pattern, 0.0, min(horizon, 130.0))
    assert pattern.expected_requests(horizon) == pytest.approx(brute, rel=2e-3)


@pytest.mark.parametrize(
    ("shape", "horizon", "peak"),
    [
        ("ramp", 100.0, 6.0),
        ("ramp", 50.0, 4.0),
        ("diurnal", 100.0, 6.0),
        ("diurnal", 25.0, 4.0),
        ("diurnal", 80.0, 6.0),
        ("black_friday", 100.0, 10.0),
        ("black_friday", 30.0, 2.0),
        ("black_friday", 45.0, 6.0),
        ("flat", 100.0, 2.0),
    ],
)
def test_peak_rps(shape: str, horizon: float, peak: float) -> None:
    assert _pattern(shape).peak_rps(horizon) == pytest.approx(peak, rel=1e-9)


def test_the_run_size_limit_counts_the_shape() -> None:
    """1,000 rps for 400 s is 400,000 requests flat and 1.12 million on
    Black Friday: the second is refused with its numbers."""

    def config(shape: str) -> SimulationConfig:
        return SimulationConfig(
            seed=1,
            duration=400.0,
            topology={"nodes": [{"name": "solo", "max_capacity": 4, "service_time": 0.1}]},
            traffic={"base_rps": 1000.0, "duration": 400.0, "shape": shape},
        )

    assert config("flat").traffic.expected_requests(400.0) == pytest.approx(400_000.0)
    with pytest.raises(ValidationError) as caught:
        config("black_friday")
    assert "1,120,000" in str(caught.value) and f"{MAX_EXPECTED_REQUESTS:,}" in str(caught.value)
    assert "5,000 rps" in str(caught.value)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_an_unknown_shape_is_refused() -> None:
    with pytest.raises(ValidationError):
        _pattern("sawtooth")


def test_a_peak_must_be_above_one() -> None:
    with pytest.raises(ValidationError):
        _pattern("ramp", shape_peak=1.0)
    with pytest.raises(ValidationError):
        _pattern("ramp", shape_peak=0.5)


def test_a_peak_on_a_flat_pattern_is_refused() -> None:
    """It would be ignored, and a field that is ignored is an error here."""
    with pytest.raises(ValidationError, match="shape_peak"):
        _pattern("flat", shape_peak=3.0)


def test_a_shape_and_exponential_growth_together_are_refused() -> None:
    with pytest.raises(ValidationError, match="exponential"):
        TrafficPattern(base_rps=2.0, duration=100.0, shape="ramp", pattern_type="exponential")


def test_the_shape_survives_a_round_trip() -> None:
    dumped = _pattern("black_friday", shape_peak=4.0).model_dump(mode="json")
    assert dumped["shape"] == "black_friday" and dumped["shape_peak"] == 4.0
    assert TrafficPattern.model_validate(dumped) == _pattern("black_friday", shape_peak=4.0)
    assert TrafficPattern(base_rps=2.0, duration=100.0).model_dump(mode="json")["shape"] == "flat"


# ---------------------------------------------------------------------------
# the arrivals
# ---------------------------------------------------------------------------


def test_an_explicit_flat_draws_what_flat_always_drew() -> None:
    pattern = TrafficPattern(base_rps=5.0, duration=20.0, shape="flat")
    gaps = np.random.default_rng(3).exponential(1.0 / 5.0, size=400)
    expected = [float(t) for t in np.cumsum(gaps) if t < 20.0]
    assert _arrivals(pattern, seed=3) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("shape", ["ramp", "diurnal", "black_friday"])
def test_arrivals_follow_the_curve(shape: str) -> None:
    """Mean arrivals per fifth of the window, over 60 seeds, against the
    integral of the rate over that fifth."""
    pattern = _pattern(shape, base_rps=4.0)
    runs = [_arrivals(pattern, seed) for seed in range(60)]
    for lo in (0.0, 20.0, 40.0, 60.0, 80.0):
        mean = float(np.mean([sum(1 for t in run if lo <= t < lo + 20.0) for run in runs]))
        assert mean == pytest.approx(_integral(pattern, lo, lo + 20.0, steps=2_000), rel=0.06), (
            shape,
            lo,
        )


def test_arrivals_stay_inside_the_window() -> None:
    pattern = _pattern("black_friday", start_time=10.0, duration=50.0)
    times = _arrivals(pattern, seed=5)
    assert times and min(times) >= 10.0 and max(times) < 60.0


def test_a_shaped_run_is_reproducible() -> None:
    pattern = _pattern("diurnal")
    assert _arrivals(pattern, seed=11) == _arrivals(pattern, seed=11)
    assert _arrivals(pattern, seed=11) != _arrivals(pattern, seed=12)


def test_a_spike_inside_a_shape_is_delivered() -> None:
    pattern = _pattern("ramp", base_rps=0.5, spike_rps=40.0, spike_start=30.0, spike_duration=5.0)
    counts = [sum(1 for t in _arrivals(pattern, seed) if 30.0 <= t < 35.0) for seed in range(40)]
    assert float(np.mean(counts)) == pytest.approx(200.0, rel=0.06)


# ---------------------------------------------------------------------------
# presets
# ---------------------------------------------------------------------------


def test_the_two_named_days() -> None:
    assert set(TRAFFIC_PRESETS) == {"black_friday", "eu_business_hours"}
    friday = traffic_preset("black_friday", base_rps=4.0, duration=120.0)
    assert friday == TrafficPattern(
        base_rps=4.0, duration=120.0, shape="black_friday", shape_peak=5.0
    )
    office = traffic_preset("eu_business_hours", base_rps=4.0, duration=120.0)
    assert office == TrafficPattern(base_rps=4.0, duration=120.0, shape="diurnal", shape_peak=3.0)


def test_an_unknown_preset_names_the_ones_that_exist() -> None:
    with pytest.raises(KeyError, match="black_friday"):
        traffic_preset("cyber_monday", base_rps=1.0, duration=10.0)


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------


def test_black_friday_breaks_the_20_node_example_harder_than_a_flat_day() -> None:
    """Same architecture, same seed, same quiet rate, no incident: only the
    shape of the day differs.

    Measured when this was written, at 4 rps: flat scores 49.8 (seed 42) and
    66.0 (seed 7) with 52 and 11 failed requests; Black Friday scores 24.5 on
    both with 484 and 438 failed."""
    base = SimulationConfig.from_yaml(str(EXAMPLES / "microservice_platform.yaml"))

    def run(shape: str, seed: int) -> dict[str, Any]:
        config = base.model_copy(
            update={
                "seed": seed,
                "chaos": [],
                "traffic": TrafficPattern(base_rps=4.0, duration=120.0, shape=shape),
            }
        )
        return CloudSimulator(config).run(suggest=False)

    for seed in (42, 7):
        flat, friday = run("flat", seed), run("black_friday", seed)
        assert friday["requests"] > 2 * flat["requests"]
        assert friday["resilience_score"] < flat["resilience_score"] - 10
        assert friday["failed_requests"] > 3 * flat["failed_requests"]
        assert friday["p95_latency"] > flat["p95_latency"]
        assert math.isfinite(friday["p95_latency"])


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def _cli_run(tmp_path: Path, *extra: str) -> dict[str, Any]:
    """``run`` on the CLI demo topology, 30 s of traffic at its own rate."""
    base = default_config()
    config = base.model_copy(
        update={
            "seed": 3,
            "duration": 30.0,
            "traffic": TrafficPattern(base_rps=base.traffic.base_rps, duration=30.0),
        }
    )
    path = tmp_path / "topology.json"
    config.to_json(str(path))
    out = tmp_path / "summary.json"
    rc = main(["run", str(path), "--report", str(tmp_path / "r.png"), "--json", str(out), *extra])
    assert rc == 0
    return dict(json.loads(out.read_text(encoding="utf-8")))


def test_the_cli_runs_a_named_day(tmp_path: Path) -> None:
    """``--traffic NAME`` swaps the config's traffic for the preset, at the
    config's own base rate and window: 2.8 times the requests on Black Friday.

    Corrected when card 3.5 was built: this compared the recorded
    ``requests``, which leave out what is still in the system at the
    horizon. Measured (seed 3, 30 s): flat 41 recorded + 4 in flight,
    Black Friday 66 + 54, business day 66 + 22. Arrivals are the count
    the shape changes."""

    def arrivals(summary: dict[str, Any]) -> int:
        # The overdue ones are recorded (as failed) and also counted unfinished.
        return int(
            summary["requests"] + summary["unfinished_requests"] - summary["overdue_requests"]
        )

    flat = arrivals(_cli_run(tmp_path))
    friday = arrivals(_cli_run(tmp_path, "--traffic", "black_friday"))
    office = arrivals(_cli_run(tmp_path, "--traffic", "eu_business_hours"))
    assert friday > 2 * flat
    assert flat < office < friday


def test_the_cli_refuses_a_day_it_does_not_know(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        _cli_run(tmp_path, "--traffic", "cyber_monday")
    assert caught.value.code == 2
    assert "black_friday" in capsys.readouterr().err
