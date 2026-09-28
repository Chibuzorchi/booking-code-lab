# Analysis documentation

- [Next-phase plan](NEXT_PHASE_PLAN.md): implemented harvesting/selection and
  planned output profiles, settlement evidence, and later grading.
- [Implementation plan](RISK_ENGINE_IMPLEMENTATION_PLAN.md): detailed constraints,
  verification requirements, and a longer-term package architecture.
- [Original design](RISK_ENGINE_DESIGN.md): design rationale and historical options.

The root [workspace README](../../README.md) describes the actual directory
layout. Architecture trees in the older design documents are proposals, not the
current filesystem. Their past command transcripts and paths retain historical
context. Provider source is now reached from this project via `../sportybet/` and
`../bet9ja/`; sports-skills via `../sports-skills/`; archived research via
`../archive/gamble/`.

## Relocation on 2026-09-22

| Old path under Sport/ | New path under Sport/ |
| --- | --- |
| `portfolio/` | `ticket-analysis/portfolio/` |
| `configs/` | `ticket-analysis/configs/` |
| `tests/` | `ticket-analysis/tests/` |
| `design/` | `ticket-analysis/docs/` |
| `reports/` | `ticket-analysis/reports/` |
| `README.md` (analysis guide) | `ticket-analysis/README.md` |
| `gamble/` | `archive/gamble/` |

Historical reports, manifests, input snapshots, and provider results retain their
original bytes. Recorded source paths therefore describe their original locations.
When replaying, supply the relocated `reports/<run>/inputs/` and
`reports/<run>/policy.json` explicitly and choose a new output directory; do not
rewrite old manifests to make their paths look current.

Provider directories and the independent sports-skills checkout were not moved.
