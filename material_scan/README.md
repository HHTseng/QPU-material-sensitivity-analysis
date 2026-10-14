# Scientific notes and checking data

Execution now uses the eight scripts at the repository root. See the
[main README](../README.md) for commands and
[WORKFLOW_VERIFICATION.md](../WORKFLOW_VERIFICATION.md) for comparisons.

This directory preserves the scientific definition, earlier results, recorded
spatial designs, figures, and numerical test fixtures. The earlier Python
package is retained in Git at
`c8cdb996209711bef0e266c7315bfa518eda47e8`.

- [science.md](docs/science.md): equations, assumptions and physical limits.
- [results.md](docs/results.md): experimental findings and unresolved questions.
- [data.md](docs/data.md): preservation and recovery.

Tests require the saved hit and spatial fixtures under `tests/fixtures`; missing
fixtures are failures. Earlier numerical findings are unchanged by this source
restructure and remain subject to their stated physical limitations.
