"""Request/response schemas for the Eleven API.

Pydantic is used here strictly for *config/schema validation* — the
simulation itself never touches these models. :class:`SimulationConfig`
is reused directly from :mod:`sim_core.config`, so a request body is
validated by the exact same rules the CLI and the library use.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from sim_core import SimulationConfig


class SimulateRequest(BaseModel):
    """One simulation run.

    ``playbook`` (optional) appends a named incident's chaos events to
    ``config.chaos`` (see :mod:`sim_core.playbooks`); an
    unknown name is a 404. ``include_report_png`` returns the rendered
    report as base64 so the frontend can display it without extra calls.

    ``include_timeseries`` returns the per-tick timeline (p50/p95 latency
    windows + per-component utilisation) so the frontend can draw the
    latency-vs-chaos timeline from one response.

    Multi-seed confidence (roadmap 1.1): ``n_seeds > 1`` runs the engine
    once per seed (seed = ``config.seed`` + i, or 42 + i when the config
    has no seed) and the response carries ``seeds`` / ``runs`` /
    ``profile`` in addition to ``summary`` (the typical run's). An
    explicit ``seeds`` list overrides ``n_seeds`` when both are given.
    ``n_seeds == 1`` (the default) leaves the response shape unchanged.
    """

    config: SimulationConfig
    playbook: str | None = None
    include_report_png: bool = False
    include_timeseries: bool = False
    n_seeds: int = Field(
        1,
        ge=1,
        le=20,
        description=(
            "How many seeds to run (1 = single run, default). Seeds are "
            "config.seed + 0..n-1, or 42 + 0..n-1 when the config has no seed."
        ),
    )
    seeds: list[int] | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="Explicit seed list; wins over n_seeds when both are set.",
    )


class SimulateResponse(BaseModel):
    """The run's full summary dict plus (optionally) the report PNG.

    ``chaos`` is the *effective* chaos schedule — the config's own events
    plus any playbook events appended server-side — so a frontend can derive
    chaos windows from the response alone. ``timeseries`` is present only
    when ``include_timeseries`` was requested.

    Multi-seed confidence runs (``n_seeds > 1`` or explicit ``seeds``) add
    three optional keys — ``seeds``, ``runs`` (one ``{seed, summary}`` per
    run) and ``profile`` (worst/typical/best, see :mod:`sim_core.profile`) —
    and set ``summary`` to the typical run's. Single-run responses omit
    them entirely (``response_model_exclude_unset`` keeps the byte shape
    identical to the pre-1.1 contract).
    """

    summary: dict[str, Any]
    report_png_b64: str | None = None
    chaos: list[dict[str, Any]] = Field(default_factory=list)
    timeseries: list[dict[str, Any]] | None = None
    seeds: list[int] | None = None
    runs: list[dict[str, Any]] | None = None
    profile: dict[str, Any] | None = None


class CompareRequest(BaseModel):
    """A compare-runs request; the mode-specific fields differ per mode.

    - ``multi-cloud`` — no extra fields; the baseline plus the same
      architecture re-calibrated with AWS / Azure / GCP presets.
    - ``what-if``     — ``set``: a non-empty list of ``"path=value"``
      strings (e.g. ``"app_worker.max_capacity=20"``), each applied to a
      copy of the baseline.
    - ``sweep``       — ``param`` (a ``set_path`` path) + ``values`` (at
      least two), plus optional ``metric`` / ``threshold`` for the knee.
    """

    config: SimulationConfig
    mode: Literal["multi-cloud", "what-if", "sweep"]
    set: list[str] | None = Field(default=None, description="what-if: 'path=value' strings")
    param: str | None = Field(default=None, description="sweep: parameter path to sweep")
    values: list[float] | None = Field(default=None, description="sweep: values to try")
    metric: str = Field(default="p95_latency", description="sweep: knee metric")
    threshold: float = Field(default=0.05, description="sweep: knee improvement fraction")


class CompareResponse(BaseModel):
    """The baseline-relative diff, plus the sweep knee when the mode has one."""

    mode: str
    diff: dict[str, Any]
    knee: float | None = None


class PlaybookInfo(BaseModel):
    """One named incident: the key and a human-readable description."""

    name: str
    description: str


class PlaybookListResponse(BaseModel):
    playbooks: list[PlaybookInfo]
    count: int


class PresetListResponse(BaseModel):
    """Provider presets dumped to plain JSON (each keyed by preset name)."""

    presets: dict[str, dict[str, Any]]
    count: int


class ImportRequest(BaseModel):
    """One architecture upload: raw text plus (optionally) its filename.

    Accepted formats (auto-detected): Eleven native YAML/JSON
    (``examples/api_stack.yaml`` shape) or ``terraform show -json`` output.
    """

    content: str = Field(..., min_length=1, description="The file text (YAML, JSON, or terraform show -json output).")
    filename: str | None = Field(None, description="Original filename, for labeling only.")


class ImportResponse(BaseModel):
    """The imported, fully-validatable config plus what was understood/assumed.

    ``config`` can be dropped straight into ``POST /simulate``. ``report``
    carries the node/edge summary, the estimate assumptions (Terraform path
    labels every filled-in parameter), warnings (invented traffic, skipped
    resources), and the auto-detected source format.
    """

    format: str
    config: SimulationConfig
    report: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    version: str
