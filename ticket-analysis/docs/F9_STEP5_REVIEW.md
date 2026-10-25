# F9 step 5 review — 24 October 2026

Reviewed HEAD `7c32bde`, including the adapter, cache, job store, orchestration
and router/transport changes. No implementation edits, live source requests,
API credentials or real-ticket outcome claims were used in this review.
Reproductions use temporary files, fake clients, controlled thread barriers and
the actual localhost HTTP handler.

Verdict: **not ready for step-6 acceptance**. Several building blocks work, but
the published HTTP path cannot yet match real legs, and ownership, cancellation,
population preservation and replay guarantees remain incomplete.

## Verification

- Full suite: **518 tests passed** in 72.230 seconds, with the existing
  ResourceWarning and expected negative-test messages.
- Actual local HTTP smoke: submit returned 202; job, latest report, codes and
  cancellation of an already-completed job returned 200. The finished matching
  test fixture still produced `unresolved / fixture_not_matched` at leg level.
- Same-run concurrent submissions share an active job in the ordinary tested
  launch path. Revision manifests and response hashes are checked when read.
- The new 17-test file exercises orchestration/helpers with fake sources; it does
  not drive the five routes through `serve.py`. The smoke above supplements that
  coverage but is not a replacement for the negative/concurrency cases below.

## S5-01 — P1: the real HTTP pipeline has no identity-matching configuration

Locations: `results_api.py:165`, `:171`, `:416`.

Every evaluation sets `aliases=[]` and `verified_mappings=[]`. The matcher requires
verified mappings or registered exact canonical aliases; its fuzzy tier never
accepts a match. Consequently no supported football leg can match through this
HTTP path, even when source names, date and final score are correct.

An actual HTTP test with correctly capitalized `Soccer`, a finished Arsenal–Chelsea
source fixture and a home-win selection published a succeeded job whose leg was
`fixture_not_matched`, tier `fuzzy_review`. The existing end-to-end unit helper
defaults to lowercase `football`, which can stop even earlier at unsupported sport;
asserting only an unresolved ticket count does not verify useful matching.

Conversion also discards every original native event ID and specifier, and changes
all kickoff interpretations to date-only. Bet9ja bare Over/Under picks require the
specifier to determine their line; explicit offset evidence is also lost.

Required: pin reviewed server-side alias/mapping evidence into input/config identity,
preserve original selection identifiers/lines/time precision, and verify at least
one hand-confirmed matched leg through the actual HTTP path. Do not solve this by
auto-accepting fuzzy names or promoting unknown ticket types into wins.

## S5-02 — P1: malformed retained rows disappear from the denominator

Location: `results_api.py:116`.

`load_population()` filters qualifying rows with `if isinstance(ticket, dict)`.
A three-row extract `[valid_ticket, null, 42]` becomes a one-row population.
Malformed selections are similarly filtered during audit/evidence conversion.
This contradicts the full-retained-population contract, even when such rows cannot
be settled.

Required: preserve stable row/selection identities and explicit invalid/unresolved
reasons, or fail the input without publishing a reduced-population report. Verify
the final accounted population against the frozen source row set, not just the
already-filtered dictionary length.

## S5-03 — P1: server restart leaves jobs permanently running

Locations: `results_api.py:331` and `jobs.py:188`.

The status/index survives a server restart, but its background daemon thread does
not. `create_or_reuse()` returns the persisted nonterminal job, while submission
claims only STARTING jobs. A RUNNING job with no remaining worker is returned
forever without launching recovery or resuming the inner job.

Reproduction: persist the correct run/input/config identity as RUNNING, reopen
JobStore, then submit again. The returned job is RUNNING and no worker thread is
started. Cancellation also has no worker to settle that abandoned record.

Required: durable worker ownership and safe recovery/reclaim of abandoned outer
and inner work, or explicit terminal recovery errors followed by resumable retry.
Test process death and a new server process; a manually called generic runner is
not the API restart path.

## S5-04 — P1: cancellation can still publish a new successful revision

Locations: `results_api.py:405` and `:427`.

The last cancellation check happens before evaluation. Cancellation received after
that check is not checked/serialized against publication or the final success write.

Reproduction: request cancellation immediately before `publish_revision()` writes.
The job ends `succeeded`, with `cancel_requested=true`, and latest.json points to
the newly published report. This is separate from correctly propagating a cancel
request to the recorded inner job.

Required: define and serialize the publication commit point with cancellation.
A cancellation accepted before that point must not publish a new latest revision.
Test barriers during evaluation and immediately before manifest/latest publication.

## S5-05 — P1: API revisions bypass the complete replay/evidence bundle

Location: `results_api.py:247`.

Publication archives converted evidence and a report, but not the committed extract,
anchored population snapshot or raw source evidence used. The digest identifies
an input whose bytes are absent. Shared mutable cache entries are not revision
archives: refresh replaces their prior content, leaving old fixture provenance
hashes without the corresponding source item/response evidence.

Reproduction using an actual HTTP-produced revision and the existing CLI replay:
replay succeeds, but ticket row_id changes from the original digest/index to null,
because no audit_population.json was archived. Only evidence/report/manifest files
exist. Normalized fixtures remain available, but original population binding and
source-response validation cannot be reconstructed independently.

Required: use a common complete publication/replay contract for CLI and HTTP,
including frozen population bytes/mapping, source evidence references/bytes,
versions/config/as-of data and counts. Replay must preserve row identities and
decisions after both original runs and the mutable cache disappear. Distinguish
canonicalized JSON from an archived original response.

## S5-06 — P1: revision IDs are filesystem paths and can cross run boundaries

Locations: `api.py:145` and `results_api.py:279`.

The revision query is joined to a path without ID validation; the loaded manifest's
run/revision identity is not compared to the request. Through the real router,
`/api/results/run-B/codes?revision=../run-A/<revision-A>` returns 200 with run-A's
ticket rows, after both runs have revision directories. An absolute revision path
can likewise escape the intended join root when matching files exist.

Required: validate run/job/revision identifiers before filesystem access, enforce
resolved-path containment, and verify manifest identity against the requested run
and revision. Test traversal and cross-run substitution through HTTP/query parsing.

## S5-07 — P2: cache replacement is not atomic for readers or single-flight

Locations: `cache.py:165`, `:213`, and `results_api.py:214`.

Writers lock and replace raw.bin then meta.json, but readers take no shared lock
or immutable-version pointer. Pause a normal refresh after the raw replacement;
a concurrent get reads old metadata plus new bytes and raises CacheError. This
can fail another evaluation even though the writer is behaving correctly.

Separately, freshness-check/fetch is not locked per cache key. Two consumers of
the same date both miss and consume two source requests/quota units. A controlled
two-thread reproduction recorded exactly two calls for one date. The daily quota
counter itself correctly serializes its increments; it does not deduplicate work.

Required: coherent versioned cache reads/publication and shared per-key fetch
ownership across runs/processes. Test readers during writes and different runs
requesting the same source date, not just duplicate submissions of one run.

## S5-08 — P2: per-call timeout does not bound runner completion

Location: `jobs.py:339`.

`future.result(timeout=...)` expires, but leaving the ThreadPoolExecutor context
waits for the running thread. A fetch blocked behind an event with a 0.02-second
budget left the runner blocked beyond 0.15 seconds, until explicitly released.
The worker is not actually abandoned as the docstring claims.

The configured transport budget is also not passed to the default API client,
which retains its independent 10-second timeout. Retries are inside the future,
so the result timeout is not a timeout applied separately to each network attempt.

Required: coherent transport/attempt/job deadlines and worker ownership semantics,
with cancellation checks before new retry calls. Test elapsed bounds and blocked
workers without leaving uncontrolled threads or starting duplicate replacement work.

## S5-09 — P2: no-result responses receive the terminal 24-hour TTL

Location: `results_api.py:232`.

An empty/no-in-scope response is marked all_terminal=True. Actual cache inspection
shows a one-day refresh interval, despite the plan assigning no-result/pending
data the shorter retry window. The same applies when in-scope data is absent but
the requested fixture is simply not yet available.

Required: classify no-result evidence separately from final results and use the
appropriate freshness policy. Refresh failure must not erase prior validated facts.

## S5-10 — P2: allow_partial does not implement partial-history evaluation

Locations: `results_api.py:102` and `:398`.

The flag never changes population loading: cancelled/failed runs with a checkpoint
are still refused by committed_extract_snapshot(). An offline registry exposing a
checkpoint but no committed extract raises RunStateError with allow_partial=True.
Instead, the flag controls whether incomplete date fetches can be published.

Required: implement the documented explicit frozen-checkpoint mode and label
population completeness; keep fetch coverage policy a separate named setting.
Include behavior-changing options in job identity. `refresh` is also accepted but
never affects behavior; define/test its semantics rather than offering an inert
control.

## S5-11 — P2: real source retries are disabled by fatal-error wrapping

Location: `results_api.py:229`.

Every ApiFootballError becomes FatalTaskError, including transport timeouts and
temporary HTTP errors. The generic runner's retry loop therefore never retries
those real-client failures. Other task errors are retried immediately without
backoff and then reported generically as budget exhaustion.

Required: distinguish retryable source/transport errors from auth/quota/schema
failures, retain structured reasons/retry timing and apply bounded backoff/source
retry instructions. Test the real adapter error contract with fake transport errors.

## Acceptance readiness

Do not count all-unmatched, all-unverified outputs as successful acceptance simply
because no incorrect ticket wins are claimed. Step 6 needs meaningful positive
matches and hand-verified leg decisions alongside explicit coverage limitations.
Unknown ticket structures should continue to prevent unsupported ticket-win claims.

Close matching/conversion, population conservation, restart/cancel, revision identity
and independent replay first. Add HTTP-level positive, negative and concurrency
tests, then run the 164-ticket acceptance corpus with source budgets kept explicit.
Only this review document was added; implementation code is unchanged.
