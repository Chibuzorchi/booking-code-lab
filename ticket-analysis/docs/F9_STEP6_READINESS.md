# F9 readiness for step 6 — 24 October 2026

Historical-product scope update: `F9_RESULTS_PLAN.md` now uses the older 8/10
October saved runs as primary product acceptance inputs. The original 164-ticket
23 October freeze below is retained as regression history. See
`F9_HISTORICAL_RESULTS_IMPLEMENTATION.md` for the implemented UI/source workflow.

The two remaining lifecycle blockers from `F9_STEP5_REREVIEW.md` have been fixed.
The next work is section 10 step 6 of `F9_RESULTS_PLAN.md`: acceptance evaluation.
This change does not perform that evaluation or establish any historical win rate.

## Cancellation and publication

Evaluation stages evidence, population and report files without a completion
manifest. The owning worker then acquires the same status lock used by cancellation,
checks that the job is active and not cancelled, and publishes the manifest/latest
pointer before recording success under that lock.

If cancellation wins before this commit, the job becomes cancelled with no new
published manifest; the previous latest report is preserved. Unpublished staged
evidence may remain on disk. If publication already owns the commit lock, a later
cancel observes the completed job and is a terminal no-op. Failed jobs cannot
invoke the publication callback.

## Worker ownership and recovery

Outer evaluation and inner fetch workers now hold kernel lifetime locks. The
outer lock is acquired under the identity lock before its job is published and
remains held through thread startup, fetching, evaluation and publication.
Progress age and stored PIDs are not ownership authorities.

An active owner is reused even when its progress timestamp is old. Process death
releases the lock through the kernel. Resubmission immediately settles an unowned
nonterminal outer record and starts its replacement; retained inner progress and
the cache allow completed fetch work to be reused. Inner fetch runners also hold
their own locks so cancellation cannot mistake active fetch work for an orphan.
A failed thread launch releases its ownership and allows a later retry.

## Regression evidence

Full suite: **579 tests passed** in 79.261 seconds. The existing unclosed-file
ResourceWarning still appeared; no tests failed. `git diff --check` also passed.

`tests/test_results_tracking_lifecycle.py` covers eight cases:

1. Cancellation during evaluation preserves the previous latest report.
2. Cancellation after staging leaves no newly published manifest.
3. Publication winning the status lock makes a simultaneous later cancel a no-op.
4. A live worker with a day-old progress timestamp is not replaced.
5. Abrupt subprocess exit releases ownership; recovery resumes inner work without
   waiting for a timeout threshold.
6. A failed job or released ownership cannot invoke publication.
7. Ownership is held before the worker thread starts, including concurrent submit
   and cancellation during the handoff.
8. Thread launch failure releases ownership and a retry succeeds.

Existing API tests now assert cancellation's effect on publication, rather than
expecting a cancelled job to publish. Their completion helper waits for ownership
to be released before tests mutate records to simulate a vanished worker.

## Step 6 execution contract

- Freeze the same 102 Bet9ja and 62 SportyBet retained tickets and identify their
  exact input digests. Use the complete retained population, with all unresolved
  rows included in accounting.
- Pin the reviewed identity configuration and source observations used by the
  acceptance run. Exercise positive accepted fixture/leg decisions as well as
  negative, ambiguous and unsupported cases in the hand-checked corpus.
- Account for every ticket with N = W + L + V + P + U. Show matching, rule/data
  coverage and unresolved reasons separately from ticket outcomes.
- Keep unverified ticket types unresolved. Do not enable a guessed accumulator
  mapping to manufacture ticket wins; verify the required provider semantics first.
- Archive replayable evidence and verify the report with original source inputs
  unavailable. Preserve numerator/denominator metrics and avoid presenting zero
  confirmed wins as proof that no winning code was ever retained.
- Keep requests within the configured source budgets. Report source-access or
  historical-coverage limitations explicitly rather than excluding affected codes.

No acceptance source requests or credential changes were made by these fixes.
