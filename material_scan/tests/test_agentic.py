from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from material_scan.agentic import (
    AgenticError,
    AgenticOptimizer,
    DEFAULT_MODEL,
    MockOllamaClient,
    preflight,
)
from material_scan.config import load_experiment_spec, load_parameter_catalog
from material_scan.search import Observation, SearchController
from material_scan.space import ExperimentSpace


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "material_scan/parameters.yaml"
EXPERIMENT = ROOT / "material_scan/experiments/material-search.yaml"


def _space() -> ExperimentSpace:
    catalog = load_parameter_catalog(CATALOG)
    return ExperimentSpace(load_experiment_spec(EXPERIMENT, catalog))


def _values(space: ExperimentSpace, fraction: float) -> dict[str, float]:
    point = space.from_unit([fraction] * space.n_cont)
    return {name: float(point[name]) for name in space.names}


def _response(space: ExperimentSpace) -> str:
    return json.dumps({
        "candidates": [
            {
                "values": _values(space, 0.25),
                "hypothesis": "moderate downconversion with conservative interfaces",
                "rationale": "Tests an interior mechanism away from the stalled corner.",
                "runtime_risk": "low",
                "runtime_reason": "Both substrate rate constants remain away from minima.",
            },
            {
                "values": _values(space, 0.75),
                "hypothesis": "stiffer substrate and stronger film interception",
                "rationale": "Tests a distinct high-stiffness interior region.",
                "runtime_risk": "medium",
                "runtime_reason": "Interior rate constants, but farther from measured anchors.",
            },
        ]
    })


def _options() -> dict[str, object]:
    return {
        "n_init": 0,
        "n_min_fit": 2,
        "refit_every": 5,
        "n_candidates": 32,
        "n_polish": 1,
        "xi": 0.0,
        "model": DEFAULT_MODEL,
        "pool_size": 2,
        "retries": 1,
    }


class _NoNetworkClient:
    def inspect_model(self):
        raise AssertionError("restore contacted Ollama for model metadata")

    def chat(self, *args, **kwargs):
        raise AssertionError("restore contacted Ollama for a proposal")


class _PreflightClient:
    size_vram = 100
    context_length = 65536

    def __init__(self, **kwargs):
        self.model = kwargs["model"]

    def inspect_model(self):
        return {"name": self.model, "digest": "pinned", "size": 100}

    def server_version(self):
        return "test-version"

    def chat(self, *args, **kwargs):
        return {"model": self.model, "content": '{"ready": true}'}

    def running_models(self):
        return [{
            "name": self.model,
            "digest": "pinned",
            "size": 100,
            "size_vram": self.size_vram,
            "context_length": self.context_length,
        }]


class AgenticTests(unittest.TestCase):
    def test_agent_pool_is_strict_and_keeps_rationale_out_of_point(self):
        space = _space()
        client = MockOllamaClient([_response(space)])
        with tempfile.TemporaryDirectory() as directory:
            optimizer = AgenticOptimizer(
                space, seed=7, client=client, trace_dir=directory, **_options()
            )
            optimizer.tell(space.baseline_point(), Observation(0.004, 0.0002))
            optimizer.tell(
                space.from_unit([0.5] * space.n_cont), Observation(0.003, 0.0002)
            )

            point = optimizer.ask(1)[0]
            schemas = {
                json.loads(path.read_text())["schema"]
                for path in Path(directory).glob("*.json")
            }

        self.assertTrue(space.in_bounds(point)[0])
        self.assertNotIn("rationale", point)
        provenance = optimizer.provenance_of(point)
        self.assertEqual(provenance["proposal_source"], "agent_gp_ei")
        self.assertIn("interior", provenance["rationale"])
        self.assertEqual(provenance["agent_model_digest"], "mock-digest")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(schemas, {
            "material-scan-agent-call-1", "material-scan-agent-selection-1"
        })

    def test_out_of_bounds_candidate_is_rejected_not_clipped(self):
        space = _space()
        optimizer = AgenticOptimizer(
            space, seed=8, client=MockOllamaClient([]), **_options()
        )
        item = json.loads(_response(space))["candidates"][0]
        name = space.names[0]
        item["values"][name] = next(
            variable.high for variable in space.variables if variable.name == name
        ) * 2.0

        candidates, errors = optimizer._parse_candidates(
            json.dumps({"candidates": [item]}), "prompt-hash"
        )

        self.assertEqual(candidates, [])
        self.assertTrue(any("outside" in error for error in errors))

    def test_normalized_key_keeps_tiny_log_parameters_distinct(self):
        space = _space()
        optimizer = AgenticOptimizer(
            space, seed=10, client=MockOllamaClient([]), **_options()
        )
        first = _values(space, 0.5)
        second = dict(first)
        second["sub_scat"] = next(
            variable.high for variable in space.variables if variable.name == "sub_scat"
        )
        second["sub_decay"] = next(
            variable.high for variable in space.variables if variable.name == "sub_decay"
        )

        def candidate(values):
            return {
                "values": values,
                "hypothesis": "isolate substrate scattering and decay",
                "rationale": "Hold every other searched coordinate fixed.",
                "runtime_risk": "medium",
                "runtime_reason": "The rate constants span a wide log interval.",
            }

        candidates, errors = optimizer._parse_candidates(
            json.dumps({"candidates": [candidate(first), candidate(second)]}),
            "prompt-hash",
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(
            optimizer._key(candidates[0].point), optimizer._key(candidates[1].point)
        )

    def test_transcript_restore_replays_agent_decision_without_network(self):
        space = _space()
        first_client = MockOllamaClient([_response(space)])
        with patch(
            "material_scan.agentic.OllamaClient",
            side_effect=[first_client, _NoNetworkClient()],
        ):
            original = SearchController(
                "agentic", seed=11, space=space, options=_options()
            )
            first = space.baseline_point()
            second = space.from_unit([0.5] * space.n_cont)
            original.tell(first, 0.004, se=0.0002, cost_seconds=10.0)
            original.tell(second, 0.003, se=0.0002, cost_seconds=12.0)
            point = original.ask(1)[0]
            original.tell(point, 0.0025, se=0.0002, cost_seconds=11.0)

            restored = SearchController.restore(original.checkpoint(), space=space)

        self.assertEqual(restored.checkpoint(), original.checkpoint())
        self.assertEqual(len(first_client.calls), 1)

    def test_pending_agent_decision_restores_without_a_second_model_call(self):
        space = _space()
        first_client = MockOllamaClient([_response(space)])
        with patch(
            "material_scan.agentic.OllamaClient",
            side_effect=[first_client, _NoNetworkClient()],
        ):
            original = SearchController(
                "agentic", seed=13, space=space, options=_options()
            )
            original.tell(space.baseline_point(), 0.004, se=0.0002)
            original.tell(
                space.from_unit([0.5] * space.n_cont), 0.003, se=0.0002
            )
            original.ask(1)

            restored = SearchController.restore(original.checkpoint(), space=space)

        self.assertEqual(restored.checkpoint(), original.checkpoint())
        self.assertEqual(len(restored.optimizer.pending), 1)
        self.assertEqual(len(first_client.calls), 1)

    def test_fallback_transcript_restores_model_metadata_without_network(self):
        space = _space()
        first_client = MockOllamaClient([AgenticError("offline")])
        options = _options()
        options["allow_model_fallback"] = True
        with patch(
            "material_scan.agentic.OllamaClient",
            side_effect=[first_client, _NoNetworkClient()],
        ):
            original = SearchController(
                "agentic", seed=12, space=space, options=options
            )
            original.tell(space.baseline_point(), 0.004, se=0.0002)
            original.tell(
                space.from_unit([0.5] * space.n_cont), 0.003, se=0.0002
            )
            point = original.ask(1)[0]
            self.assertEqual(
                original.optimizer.provenance_of(point)["proposal_source"],
                "gp_fallback",
            )
            original.tell(point, 0.0028, se=0.0002)

            restored = SearchController.restore(original.checkpoint(), space=space)

        self.assertEqual(restored.checkpoint(), original.checkpoint())
        self.assertEqual(restored.optimizer.model_digest, "mock-digest")
        self.assertEqual(restored.optimizer.ollama_version, "mock-version")
        self.assertEqual(len(first_client.calls), 1)

    def test_strict_mode_does_not_disguise_model_failure_as_agentic(self):
        space = _space()
        optimizer = AgenticOptimizer(
            space,
            seed=9,
            client=MockOllamaClient([AgenticError("offline")]),
            **_options(),
        )
        optimizer.tell(space.baseline_point(), Observation(0.004, 0.0002))
        optimizer.tell(space.from_unit([0.5] * space.n_cont), Observation(0.003, 0.0002))
        with self.assertRaisesRegex(AgenticError, "usable pool"):
            optimizer.ask(1)

    def test_preflight_requires_exact_residency_and_context(self):
        with patch("material_scan.agentic.OllamaClient", _PreflightClient):
            result = preflight(
                model=DEFAULT_MODEL,
                expected_digest="pinned",
                num_ctx=65536,
                warmup=True,
            )
            self.assertTrue(result["ready"])
            self.assertTrue(result["fully_on_gpu"])
            self.assertTrue(result["context_matches"])

            _PreflightClient.size_vram = 99
            partial = preflight(
                model=DEFAULT_MODEL,
                expected_digest="pinned",
                num_ctx=65536,
                warmup=True,
            )
            self.assertFalse(partial["ready"])
            self.assertFalse(partial["fully_on_gpu"])
            _PreflightClient.size_vram = 100


if __name__ == "__main__":
    unittest.main()
