from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from material_scan.runner import (
    RunError,
    inspect_artifacts,
    normalized_macro_bytes,
    run_task,
    validate_macro_lines,
)


class RunnerTests(unittest.TestCase):
    def test_macro_identity_normalizes_only_attempt_paths(self):
        left = "/g4cmp/HitsFile /a/x.partial\n/run/beamOn 10\n/control/shell touch /a/x.done\n"
        right = "/g4cmp/HitsFile /b/y.partial\n/run/beamOn 10\n/control/shell touch /b/y.done\n"
        changed = right.replace("beamOn 10", "beamOn 11")
        self.assertEqual(normalized_macro_bytes(left), normalized_macro_bytes(right))
        self.assertNotEqual(normalized_macro_bytes(left), normalized_macro_bytes(changed))

    def test_numeric_check_matches_known_bad_token(self):
        self.assertEqual(
            validate_macro_lines(["/g4cmp/clearance 1e-06e-6 mm\n"]),
            ["/g4cmp/clearance 1e-06e-6 mm"],
        )

    def test_zero_exit_without_witness_is_failure(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with self.assertRaisesRegex(RunError, "without the post-beamOn witness"):
                run_task(
                    [sys.executable, "-c", "from pathlib import Path; Path(r'%s').write_text('header\\n')" % (root / "x.partial")],
                    partial_output=root / "x.partial",
                    final_output=root / "x.hits",
                    completion_witness=root / "x.witness",
                    log_path=root / "x.log",
                    timeout_s=5,
                )
            self.assertFalse((root / "x.hits").exists())

    def test_valid_task_is_published_and_witness_removed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            partial = root / "x.partial"
            witness = root / "x.witness"
            code = (
                "from pathlib import Path; "
                f"Path(r'{partial}').write_text('header\\n'); "
                f"Path(r'{witness}').touch()"
            )
            result = run_task(
                [sys.executable, "-c", code],
                partial_output=partial,
                final_output=root / "x.hits",
                completion_witness=witness,
                log_path=root / "x.log",
                timeout_s=5,
            )
            self.assertEqual(result.output.read_text(), "header\n")
            self.assertFalse(witness.exists())
            self.assertEqual(len(result.output_sha256), 64)

    def test_restart_distinguishes_ready_and_already_published(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            partial = root / "x.partial"
            final = root / "x.hits"
            witness = root / "x.witness"
            partial.write_text("header\n")
            witness.touch()
            self.assertEqual(
                inspect_artifacts(
                    partial_output=partial,
                    final_output=final,
                    completion_witness=witness,
                ).state,
                "ready-to-publish",
            )
            partial.replace(final)
            witness.unlink()
            self.assertEqual(
                inspect_artifacts(
                    partial_output=partial,
                    final_output=final,
                    completion_witness=witness,
                ).state,
                "published-uncommitted",
            )


if __name__ == "__main__":
    unittest.main()
