# Run history investigation — 24 October 2026

Scope: investigate apparently missing codes, yesterday versus today, pagination,
distinct analysis, history reloads, and runs ending with `try_budget` or cancellation.
This investigation changes no application behavior or historical data.

## Findings from the saved runs

All 27 registered committed extracts on this machine passed SHA-256, byte-size,
and saved-row-count checks against their status records. This establishes that
these files match their committed records; it cannot establish that every decoded
provider response was originally retained.

| Run / seed | Recorded odds band | Saved codes | Eligible at run finish* | Eligible during investigation | Fully distinct during investigation |
| --- | --- | ---: | ---: | ---: | ---: |
| Bet9ja 23 Oct / `5WXNY8B` | 100,000–400,000 | 102 | 102 | 17 | 5 |
| SportyBet 23 Oct / `T97PMW` | 100,000–400,000 | 62 | 62 | 18 | 7 |
| Bet9ja 24 Oct / `5X2ZT6S` | 10,000–150,000 | 334 | 332 | 332 | 26 |

*Reconstructed by running the existing pool builder with its clock fixed to the
recorded completion timestamp. These are reconstructions, not captured browser
screenshots. Counts checked with the current clock can decrease after this report.

Today's Bet9ja run is `run-bet9ja-20261024-051623-b93204b5`.
Its 332 eligible codes occupy 14 pages at 25 per page; its 26 fully distinct codes
occupy two pages. The other 306 eligible codes overlap another ticket on at least
one game+option. They remain in the saved extract and Stage 1 pool.
Two additional saved tickets were already excluded for kickoff expiry at completion.

Yesterday and today used different seeds and different odds bands. Their raw
harvest totals are therefore not comparable as identical searches.

## Why a saved run appears to shrink

`portfolio/api.py` sends `/api/codes` through `build_pool()`, just like the distinct
and decorrelation analyses. That builder calls `normalize_ticket()`.
`portfolio/analysis.py` checks kickoff dates against `datetime.now(timezone.utc)`
and adds an invalid-ticket issue when a leg has kicked off. `build_pool()` excludes
any ticket with an issue.

Consequently, Stage 1 history is currently a current-eligibility view over saved
data, even though its copy describes codes that the run found. It is not a complete
historical listing. Yesterday's Bet9ja extract still contains 102 rows, but 85 are
now hidden by expiry; yesterday's SportyBet extract still contains 62, but 44 are
now hidden by expiry. No extract deletion is needed to produce this effect.

The console keeps fetched reports in each tab's memory. For a succeeded run,
`loadCodes()` fetches once for its run/state/qualifying-count stamp. There is no
periodic expiry refresh after success. Another tab or a reload fetches a newly
filtered pool. Thus two tabs can show different counts for the same unchanged
extract. Provider-specific odds view filters can also differ between tabs.
The exact original tab state was not available, so its precise contribution
cannot be proven retrospectively.

Loading history on another page and seeing all 14 pages is consistent with the
current 332-code eligible pool. History and initial loading both use `/api/codes`;
history does not load a separate distinct-only saved file.

## What `try_budget` means

The scanner stops scheduling candidates when it reaches `max_codes_to_try`, drains
the outstanding results, sets `stop_reason = try_budget`, then saves the result.
The scan entrypoint returns success. The registry commits the full qualifying
extract and marks the run `succeeded`.

Today's 334-code run and yesterday's runs all successfully committed their
extracts. `try_budget` is a normal work limit, not an unfinished save or an error.

## Confirmed loss on cancellation

SportyBet run `run-sportybet-20261009-094422-ed9c78b4` has:

- 18 qualifying hits in its counters and 18 `HIT` lines in `worker.log`.
- Only 12 full ticket records in `checkpoint.json`.
- Worker completion evidence showing exit code `-15` (SIGTERM).
- Six logged codes absent from the checkpoint: `XGF33M`, `XGF33L`, `XGF340`,
  `XGF34G`, `XGF34R`, `XGF350`.

Checkpoints are throttled and written on qualifying hits. A normal Python
exception unwinds through the scan's `finally` block and forces a checkpoint.
Default SIGTERM termination does not run that block; cancellation can therefore
lose hits since the last successful checkpoint. The code strings and hit summaries
above remain recoverable from the log, but their complete historical ticket
contents are absent from the checkpoint. Fetching them now would be a new
observation, not recovery of their original full snapshots.

This is a real persistence gap, separate from today's successful budget stop.

## Additional limit on “save everything”

Both provider scanners discard identical-content coupons before appending to the
qualifying list: a `seen_fingerprints` set retains only one code for a selection
fingerprint, including suppressing aliases of the seed's contents. They also apply
the configured odds/leg band and simulation filters before retention; SportyBet
rejects unavailable coupons. Therefore the archive currently preserves the
scanner's qualifying, content-deduplicated output, not every valid booking code
decoded. This is different from Stage 2's stricter game+option overlap analysis.

## Verification

- Verified all 27 committed extracts against their recorded digest, size, and count.
- Replayed the three recent extracts at completion time and investigation time.
- Drove the actual console JavaScript through all 14 pages with 332 codes: 13 pages
  of 25 plus a final page of seven, all 332 unique codes reachable; Copy all copied
  all 332 despite pagination.
- Existing console/page suite: 33 tests passed.
- Existing checkpoint exception-preservation test passed for both providers.
  This test covers Python exceptions, not SIGTERM; the historical cancellation
  evidence exposes that missing coverage.

## Recommended corrections

1. Make Stage 1 history list every retained historical code, with expiry and other
   eligibility reasons visible on the rows. Keep explicit eligible/expired counts.
2. Make the time basis of analysis explicit and ensure tab refresh behavior agrees
   with that time basis. Preserve the historical saved count separately.
3. Persist every discovered qualifying booking-code alias before content analysis,
   if “everything” includes different codes with identical ticket contents. Keep
   content deduplication and distinct analysis as separate views.
4. Persist accepted hits durably before advertising them as saved, and handle
   cancellation gracefully. Use a per-hit journal or equivalent durable design
   for abrupt termination; a signal handler alone cannot protect against SIGKILL
   or process crashes. Surface persistence failures rather than silently ignoring
   them. Add subprocess cancellation/crash and same-count checkpoint refresh tests.

No fix has been applied as part of this investigation.

Follow-up: after the user's request to reveal historical tickets, Stage 1 gained
**Show expired codes**. It displays saved tickets excluded solely by kickoff
expiry, labels their outcomes as unchecked, and supports pagination and copying.
The original snapshots are preserved; the analysis pools continue to exclude them.
A subsequent replay returned all 102 rows for yesterday's Bet9ja run (17 upcoming
+ 85 expired) and all 62 for yesterday's SportyBet run (18 upcoming + 44 expired).
The per-code proof, including earliest kickoff timestamps and extract digests,
is in `results/investigations/2026-10-24-expired-history-proof.json` at workspace root.
