from __future__ import annotations

import unittest

import numpy as np

from material_scan.search import SearchController, available


class SearchTests(unittest.TestCase):
    def test_expected_methods_exist(self):
        self.assertTrue(
            {"random", "sobol", "bo_gp", "cmaes", "agentic"}.issubset(available())
        )

    def test_transcript_restore_preserves_random_stream(self):
        original = SearchController("random", seed=17)
        first = original.ask(2)
        original.tell(first[0], 0.003, se=0.0001)
        original.reject(first[1], "synthetic physical rejection")
        original.ask(3)
        record = original.checkpoint()

        restored = SearchController.restore(record)
        self.assertEqual(original.ask(2), restored.ask(2))

    def test_cma_policy_is_synchronous(self):
        self.assertEqual(
            SearchController("cmaes", seed=1).scheduler_policy,
            "synchronous-generation",
        )

    def test_cma_initial_mean_is_recorded_and_replayed(self):
        base = SearchController("cmaes", seed=9)
        mean = np.linspace(0.1, 0.9, base.optimizer.d).tolist()
        original = SearchController(
            "cmaes", seed=9, options={"mean0": mean, "popsize": 12}
        )
        self.assertTrue(np.allclose(original.optimizer.mean, mean))
        restored = SearchController.restore(original.checkpoint())
        self.assertEqual(original.ask(3), restored.ask(3))

    def test_cma_sequential_driver_waits_for_the_complete_population(self):
        search = SearchController("cmaes", seed=19, options={"popsize": 12})
        for index in range(11):
            point = search.ask(1)[0]
            search.tell(point, 0.01 + index * 0.001)
            self.assertEqual(search.optimizer.generation, 0)
        final = search.ask(1)[0]
        search.tell(final, 0.02)
        self.assertEqual(search.optimizer.generation, 1)
        self.assertEqual(search.optimizer.state()["n_used_in_update"], 12)

    def test_gp_waits_for_declared_external_starting_results(self):
        points = SearchController("random", seed=4).ask(2)
        search = SearchController(
            "bo_gp", seed=5, options={"n_init": 0, "n_min_fit": 2}
        )
        self.assertEqual(search.ask(1), [])
        search.tell(points[0], 0.003, se=0.0001)
        self.assertEqual(search.ask(1), [])
        search.tell(points[1], 0.002, se=0.0001)
        self.assertEqual(len(search.ask(1)), 1)


if __name__ == "__main__":
    unittest.main()
