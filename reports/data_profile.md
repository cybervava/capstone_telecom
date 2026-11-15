# Data profile: customer → service → node mapping

Generated 2026-11-15 16:34 UTC from `data/generated`. Mapping version(s): 2026-11-01.

A service is **resolved** when its address has exactly one current mapping row and that row points at an active node. Everything else cannot be matched to an outage from the mapping alone.

| Service type | Services | Resolved | Coverage | Missing | Ambiguous | Stale |
|---|---:|---:|---:|---:|---:|---:|
| broadband | 180,052 | 174,402 | 96.86% | 2,622 | 1,772 | 1,256 |
| mobile | 327,952 | 317,400 | 96.78% | 4,874 | 3,296 | 2,382 |

| Node type | Status | Count |
|---|---|---:|
| AGG | ACTIVE | 77 |
| OLT | ACTIVE | 721 |
| OLT | DECOMMISSIONED | 7 |
| TOWER | ACTIVE | 217 |
| TOWER | DECOMMISSIONED | 2 |
