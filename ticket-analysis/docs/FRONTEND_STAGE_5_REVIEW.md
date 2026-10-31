# Stage 5 — scan configuration and job status

Implemented 2026-10-31 on `feat/frontend-stage-5-scan`, branched from merged main `fb14d18` (PR #11). Merged into main as PR #12; Stage 6 has not started.

Plan: [Frontend audit and incremental redesign](FRONTEND_AUDIT_AND_PLAN.md#5-scan-configuration-and-job-status).

## Changes

- Explicit native radio choice between a typed seed and fresh booking, grouped collection limits, and collapsed advanced attempt limit.
- Separate scan status area with human-readable booking, scanning, cancelling, completion and failure states. Raw run IDs, stop reasons, provider errors and excluded counts remain in diagnostics.
- Read-only Refresh status recovers failed polling and resumes updates without submitting a scan. Failed submission directs users to Saved Runs before another launch because receipt may be uncertain.
- Cancellation stays attached to its originating provider/run, prevents duplicate clicks, and ignores older status replies (including errors) after cancellation. Changing runs cannot leave the new run blocked by the old cancellation.
- Preserved API payloads/defaults, case handling, fresh/typed exclusivity, partial-code access, provider ownership and analysis gates. No worker/backend changes.

## Validation

- Full suite: 629 tests passed, including worker/API lifecycle, provider isolation, registry, reporting and results tracking.
- Expanded Chromium suite: 24 tests passed, including new seed-source payload, status-refresh recovery, pending cancellation/provider-switch, late polling reply, diagnostics and responsive checks.
- After final race guards, focused page and cancellation regression pass: 42 tests passed.
- Layout checks at 1440, 768, 390 and 320 CSS pixels; no page overflow in checked states, scan actions and seed-choice labels at least 44px high.
- Existing run navigation, history, copy, analysis, provider-switch and stale-response assertions retained. Assertions for run identity now inspect the persistent run context; raw booking errors are checked in diagnostics.
- `git diff --check` passed.

Browser requests were intercepted with fixtures. No real provider scan or fresh booking was launched. Safari, real iOS and screen-reader acceptance remain outside this validation.
