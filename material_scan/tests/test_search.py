from __future__ import annotations

import unittest

from material_scan.search import SearchController, available


class SearchTests(unittest.TestCase):
    def test_expected_methods_exist(self):
        self.assertTrue({"random", "sobol", "bo_gp", "cmaes"}.issubset(available()))

    def test_transcript_restore_preserves_random_stream(self):
        original = SearchController("random", seed=17)
        first = original.ask(2)
        original.tell(first[0], 0.003, se=0.0001)
        original.reject(first[1], "synthetic gate")
        original.ask(3)
        record = original.checkpoint()

        restored = SearchController.restore(record)
        self.assertEqual(original.ask(2), restored.ask(2))

    def test_cma_policy_is_synchronous(self):
        self.assertEqual(
            SearchController("cmaes", seed=1).scheduler_policy,
            "synchronous-generation",
        )


if __name__ == "__main__":
    unittest.main()
