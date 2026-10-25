# F9 step 5 fix re-review — 24 October 2026

Reviewed HEAD `7b9629e`, including fixes through `0729ea3` and the preceding
step-5 fix commits. Verdict: **hold step 6**. Two original P1 guarantees are
still not met. These are remaining ownership/publication failures, not new
requirements for source coverage or additional features.

Full suite: **571 tests passed** in 78.722 seconds. The same unclosed-file
ResourceWarning still appeared in this run; the assertion that this run has no
warnings was not reproduced. Expected CLI-negative-test messages also appeared.
No external API requests or credentials were used. No implementation was changed.

## R5-01 — P1: cancellation changes the terminal label but not publication

Original finding: S5-04. Location: `results_api.py`, `_commit_publication()` and
the call to `publish_revision()` immediately before it.

`_commit_publication()` now checks cancellation under the status lock, but is
called only after the revision manifest and latest.json have already been written.
It can mark the job CANCELLED while leaving the new revision publicly discoverable.
This does not preserve the last complete report when cancellation wins before
publication, which was the original required guarantee.

Reproduction: run an evaluation with no source requests required. Request cancel
at entry to `publish_revision()`, before its real implementation writes any file.
The final state is CANCELLED, but latest.json exists and points to the new report.
The original failure has changed from “succeeded despite cancellation” to
“cancelled but still published.”

The new `test_a_late_cancellation_lands_as_cancelled_not_succeeded` explicitly
expects this publication. That assertion codifies different cancellation semantics
instead of closing the review's publication race.

Required: stage evidence separately, then serialize cancellation, ownership and
the actual publication commit (including the latest pointer). If cancellation
was accepted before that commit, the previous latest revision must remain
unchanged. Evidence may be retained without publishing it as the latest result.
Add a barrier test asserting pointer/manifest behavior, not merely the job label.

## R5-02 — P1: five minutes without outer progress is not proof of worker death

Original finding: S5-03. Location: `results_api.py`, `_reclaim_if_abandoned()`.

Recovery now marks any nonterminal job with updated_at older than 300 seconds
FAILED and starts a replacement. There is no live-worker lock, lease renewal or
fencing token. Outer progress is not renewed throughout inner fetch/evaluation
work. A slow live evaluation can therefore satisfy this test; its old worker is
not stopped or prevented from publishing after the record becomes terminal.

Reproduction with a real blocked worker thread and fake source:

1. Submit a job and wait until its source fetch is actively blocked on a controlled
   event (so the worker is known to be alive).
2. Backdate only the outer record's updated_at by 301 seconds to simulate elapsed
   work time without an outer progress write; do not remove its worker.
3. Resubmit: the old job becomes FAILED and a different outer job is created.
4. Release the original fetch. Both workers continue; two revision manifests are
   published for the same run/input/config identity.

The cache's single-flight lock can avoid a duplicate source request, but it does
not restore exclusive evaluator ownership or prevent the failed worker from
publishing. A terminal status guard on later status writes cannot undo an already
performed publication side effect.

Required: establish actual ownership (for example a lifetime lock), or use a
renewable lease with fencing that prevents a superseded worker from committing
requests/results. Reclaim only when ownership is demonstrably lost. Verify the
claim while holding the relevant synchronization, not from an earlier age read.
Test both dead-process recovery and a live worker exceeding the age threshold,
and prohibit late publication by a superseded worker.

## Readiness decision

The new population, path validation, cache and retry changes add coverage, but a
green suite is not sufficient to close the P1 list while the two reproductions
above still succeed. Do not resume step-6 acceptance as if backend lifecycle
correctness is established. No step-6 work was performed in this review.

Implementation follow-up: both findings above are now fixed. Publication is
guarded by the cancellation/status lock, and worker recovery uses kernel lifetime
ownership rather than progress age. Eight lifecycle regressions and the full
579-test suite pass. The current readiness decision is recorded in
[F9_STEP6_READINESS.md](F9_STEP6_READINESS.md): begin step-6 acceptance evaluation.
