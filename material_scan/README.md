# Scientific notes and checking data

Execution uses five scripts at the repository root: `scan.py`, `physics.py`,
`analysis.py`, `search.py`, and `records.py`. See the [main README](../README.md)
for commands and [numerical verification](../verification/round2_results.json)
for the source comparison.

This directory contains scientific definitions and interpretation of historical
results. Earlier simulation files and generated result figures are archived
outside the working tree. Input-only recorded spatial designs live in
[`../studies/designs/`](../studies/designs/). Numerical fixtures live in
[`../tests/fixtures/`](../tests/fixtures/). The earlier Python package is retained in Git at
`c8cdb996209711bef0e266c7315bfa518eda47e8`.

- [science.md](docs/science.md): equations, assumptions and physical limits.
- [results.md](docs/results.md): historical experimental findings and unresolved questions.
- [data.md](docs/data.md): preservation and recovery.

Run `python -m unittest discover -s tests -v` from the repository root. Tests
require the saved hit and spatial fixtures; missing fixtures are failures.
Earlier numerical findings are unchanged by this source restructure and remain
subject to their stated physical limitations. The recorded spatial designs use
an 8 × 8 mm² injection region within a 10 × 10 mm² chip; convergence in that
region does not establish uniform whole-chip injection results.
