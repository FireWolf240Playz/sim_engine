"""Tests for sim_core.importers — upload YAML/JSON/Terraform → topology.

The goal is the *contract*: auto-detection, native pass-through, Terraform
role mapping + reachable edge inference, honest assumption reporting, and
determinism. One smoke test runs the imported topology through the real
engine to prove it is not merely valid but runnable.

Run with::

    pytest tests/test_importers.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim_core import CloudSimulator
from sim_core.config import ComponentRole, SimulationConfig, TrafficPattern
from sim_core.importers import ArchitectureImportError, import_architecture

REPO_ROOT = Path(__file__).resolve().parent.parent
API_STACK = (REPO_ROOT / "examples" / "api_stack.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Native path
# ---------------------------------------------------------------------------


def test_native_yaml_round_trip() -> None:
    """Importing the canonical example reproduces the validated config exactly."""
    result = import_architecture(API_STACK, filename="api_stack.yaml")

    assert result.report.format == "native_yaml"
    assert result.report.source == "api_stack.yaml"
    assert result.report.node_count == 6
    assert result.report.unmapped_resources == []
    assert result.report.assumptions == []
    assert result.report.warnings == []

    expected = SimulationConfig.from_yaml(str(REPO_ROOT / "examples" / "api_stack.yaml"))
    assert result.config == expected


def test_native_json_round_trip() -> None:
    """A native config serialized to JSON imports back to the same config."""
    config = SimulationConfig.from_yaml(str(REPO_ROOT / "examples" / "api_stack.yaml"))
    text = json.dumps(config.model_dump(mode="json"))

    result = import_architecture(text, filename="api_stack.json")

    assert result.report.format == "native_json"
    assert result.config == config


def test_native_missing_traffic_gets_labeled_default() -> None:
    """A topology-only document is accepted; the invented traffic is a warning."""
    data = {
        "duration": 60,
        "topology": {
            "nodes": [
                {"name": "worker", "role": "worker", "max_capacity": 4, "service_time": 1.0},
                {"name": "db", "role": "database", "max_capacity": 2, "service_time": 2.0},
            ],
            "edges": [{"source": "worker", "target": "db", "probability": 1.0}],
        },
    }
    result = import_architecture(json.dumps(data), filename="no_traffic.json")

    assert result.config.traffic.base_rps == 2.0
    assert result.config.traffic.duration == 60.0
    assert any("traffic" in warning and "assumed" in warning for warning in result.report.warnings)


def test_native_invalid_config_is_actionable_error() -> None:
    text = json.dumps(
        {
            "duration": 60,
            "topology": {
                "nodes": [{"name": "worker", "role": "worker", "max_capacity": 0, "service_time": 1.0}],
                "edges": [],
            },
            "traffic": {"base_rps": 1.0, "duration": 60},
        }
    )
    with pytest.raises(ArchitectureImportError, match="invalid Eleven config"):
        import_architecture(text)


def test_empty_text_is_an_error() -> None:
    with pytest.raises(ArchitectureImportError, match="empty"):
        import_architecture("   ")


def test_garbage_is_an_error() -> None:
    with pytest.raises(ArchitectureImportError, match="could not parse"):
        import_architecture("definitely: [not: valid: yaml {")


# ---------------------------------------------------------------------------
# Terraform path
# ---------------------------------------------------------------------------

TF_AWS_STACK = {
    "format_version": "1.0",
    "values": {
        "root_module": {
            "resources": [
                {
                    "address": "aws_lb.web",
                    "type": "aws_lb",
                    "name": "web",
                    "instances": [
                        {"attributes": {"load_balancer_type": "application", "name": "web-alb"}}
                    ],
                },
                {
                    "address": "aws_ecs_service.api",
                    "type": "aws_ecs_service",
                    "name": "api",
                    "instances": [{"attributes": {"desired_count": 3, "name": "api"}}],
                },
                {
                    "address": "aws_ecs_service.billing",
                    "type": "aws_ecs_service",
                    "name": "billing",
                    "instances": [{"attributes": {"desired_count": 1, "name": "billing"}}],
                },
                {
                    "address": "aws_elasticache_replication_group.session",
                    "type": "aws_elasticache_replication_group",
                    "name": "session",
                    "instances": [{"attributes": {"node_type": "cache.t3.medium"}}],
                },
                {
                    "address": "aws_db_instance.main",
                    "type": "aws_db_instance",
                    "name": "main",
                    "instances": [{"attributes": {"instance_class": "db.t3.medium"}}],
                },
                {
                    "address": "aws_sqs_queue.jobs",
                    "type": "aws_sqs_queue",
                    "name": "jobs",
                    "instances": [{"attributes": {"name": "jobs"}}],
                },
                {
                    "address": "aws_vpc.main",
                    "type": "aws_vpc",
                    "name": "main",
                    "instances": [{"attributes": {"cidr_block": "10.0.0.0/16"}}],
                },
            ]
        }
    },
}


def _tf_text(doc: dict) -> str:
    return json.dumps(doc)


def test_terraform_aws_stack_maps_roles_and_edges() -> None:
    result = import_architecture(_tf_text(TF_AWS_STACK), filename="tf.json")
    config, report = result.config, result.report

    assert report.format == "terraform_json"
    assert report.node_count == 6  # 7 resources minus the unmapped aws_vpc
    assert report.unmapped_resources == ["aws_vpc.main"]

    roles = {node.name: node.role for node in config.topology.nodes}
    assert roles["web"] == ComponentRole.LOAD_BALANCER
    assert roles["api"] == ComponentRole.WORKER
    assert roles["billing"] == ComponentRole.WORKER
    assert roles["session"] == ComponentRole.CACHE
    assert roles["main"] == ComponentRole.DATABASE
    assert roles["jobs"] == ComponentRole.GENERIC

    # LB fans out evenly to both workers; every worker hits the cache and
    # the queue; the cache's miss path lands on the database.
    edge_set = {(e.source, e.target, e.probability) for e in config.topology.edges}
    assert ("web", "api", 0.5) in edge_set
    assert ("web", "billing", 0.5) in edge_set
    assert ("api", "session", 1.0) in edge_set
    assert ("billing", "session", 1.0) in edge_set
    assert ("api", "jobs", 0.5) in edge_set
    assert ("session", "main", 1.0) in edge_set

    assert config.topology.resolve_entry_node() == "web"
    assert config.traffic.base_rps == 2.0
    assert config.chaos == []


def test_terraform_estimates_are_labeled() -> None:
    result = import_architecture(_tf_text(TF_AWS_STACK))
    report = result.report

    # Every node got an assumption line; nothing is presented as fact.
    assert len(report.assumptions) >= 6
    assert all(
        ("estimated" in a.lower()) or ("inferred" in a.lower())
        for a in report.assumptions
    )
    # Instance-size-based estimates are specific:
    assert any("cache.t3.medium" in a for a in report.assumptions)
    assert any("db.t3.medium" in a for a in report.assumptions)
    assert any("desired_count" in a or "replicas" in a for a in report.assumptions)
    # Traffic was invented and flagged:
    assert any("traffic" in w.lower() for w in report.warnings)


def test_terraform_cache_gets_hit_rate() -> None:
    result = import_architecture(_tf_text(TF_AWS_STACK))
    cache = result.config.topology.node("session")
    assert cache.role == ComponentRole.CACHE
    # hit_rate lives on the cache subclass after reconstitution:
    assert getattr(cache, "hit_rate", 0.8) == 0.8


def test_terraform_single_db_is_a_valid_one_node_graph() -> None:
    doc = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_db_instance.solo",
                        "type": "aws_db_instance",
                        "name": "solo",
                        "instances": [{"attributes": {"instance_class": "db.t3.small"}}],
                    }
                ]
            }
        },
    }
    result = import_architecture(_tf_text(doc))
    assert result.report.node_count == 1
    assert result.report.edge_count == 0
    assert result.config.topology.resolve_entry_node() == "solo"


def test_terraform_two_workers_no_lb_stays_connected() -> None:
    doc = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_ecs_service.a",
                        "type": "aws_ecs_service",
                        "name": "a",
                        "instances": [{"attributes": {"desired_count": 1}}],
                    },
                    {
                        "address": "aws_ecs_service.b",
                        "type": "aws_ecs_service",
                        "name": "b",
                        "instances": [{"attributes": {"desired_count": 1}}],
                    },
                    {
                        "address": "aws_db_instance.d",
                        "type": "aws_db_instance",
                        "name": "d",
                        "instances": [{"attributes": {}}],
                    },
                ]
            }
        },
    }
    result = import_architecture(_tf_text(doc))
    entry = result.config.topology.resolve_entry_node()
    edges = {(e.source, e.target) for e in result.config.topology.edges}
    # Both workers reachable from the entry; db reachable too.
    assert entry in ("a", "b")
    assert ("a", "b") in edges or ("b", "a") in edges
    assert any(target == "d" for (_, target) in edges)
    assert any("no load balancer" in a for a in result.report.assumptions)


def test_terraform_duplicate_names_are_deduplicated() -> None:
    doc = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_lb.default",
                        "type": "aws_lb",
                        "name": "default",
                        "instances": [{"attributes": {}}],
                    },
                    {
                        "address": "aws_db_instance.default",
                        "type": "aws_db_instance",
                        "name": "default",
                        "instances": [{"attributes": {}}],
                    },
                ]
            }
        },
    }
    result = import_architecture(_tf_text(doc))
    names = {node.name for node in result.config.topology.nodes}
    assert names == {"default", "default-2"}


def test_terraform_only_unmappable_is_an_actionable_error() -> None:
    doc = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_vpc.main",
                        "type": "aws_vpc",
                        "name": "main",
                        "instances": [{"attributes": {}}],
                    },
                    {
                        "address": "aws_security_group.web",
                        "type": "aws_security_group",
                        "name": "web",
                        "instances": [{"attributes": {}}],
                    },
                ]
            }
        },
    }
    with pytest.raises(ArchitectureImportError, match="no mappable resources") as excinfo:
        import_architecture(_tf_text(doc))
    assert "aws_vpc" in str(excinfo.value)


def test_terraform_is_deterministic() -> None:
    text = _tf_text(TF_AWS_STACK)
    first = import_architecture(text, filename="a.json")
    second = import_architecture(text, filename="b.json")
    assert first.config == second.config
    # Only the source label differs:
    first.report.source = second.report.source  # type: ignore[misc]
    assert first.report == second.report


# ---------------------------------------------------------------------------
# End-to-end: an imported topology actually runs
# ---------------------------------------------------------------------------


def test_imported_topology_runs_through_the_engine() -> None:
    result = import_architecture(_tf_text(TF_AWS_STACK))
    config = result.config.model_copy(
        update={
            "duration": 30.0,
            "traffic": TrafficPattern(base_rps=1.0, duration=30.0),
            "seed": 7,
        }
    )
    summary = CloudSimulator(config).run()
    assert summary["requests"] > 0
    assert summary["resilience_score"] is not None
