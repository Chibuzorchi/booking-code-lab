# Historical results: usage and implementation

Implemented against the gaps recorded in `F9_HISTORICAL_RESULTS_GAP_REVIEW.md`.
The backend and UI now operate on old saved runs without a new scan, distinct
analysis or decorrelation. No live API requests were used during implementation.

## Using the UI

1. Start/restart the normal `portfolio.serve` process and open `/app`.
2. Expand **Earlier runs on this machine**, select a run and choose **Use this run**.
3. Use **Check result** directly on a saved code card, **Check all saved codes**
   above the list, or **Check selected run’s results** beside the history selector.
   A code check focuses its own result; **Show all saved codes** returns to the run.
   Alternatively, choose **Open saved results**. Every retained row is available, including
   started and overlapping codes. Stage 1's expired toggle does not affect this list.
4. Choose **Check result** on a code, or **Check all results** for the saved run.
5. Expand **Original picks and outcome evidence** for scores, selected market,
   period facts, matching candidates/reasons, source identity and observation time.
6. Filter outcomes or move between pages. The run's numerator/denominator and
   headline rates do not change. Unchecked rows count as unresolved in that total.

An existing report remains visible while a refresh runs. Cancelled/failed work
does not replace it. A stopped run needs explicit **Use incomplete saved history**;
the report then states that its retained population is incomplete.

## Server configuration and historical imports

The server reads `results/results_tracking/config.json` under the workspace, or
the file named by `F9_RESULTS_CONFIG`. This is a server-owned configuration file;
API clients cannot supply paths, mappings or settlement overrides. Restart the
server after enabling network access or changing the configuration path.

`F9_RESULTS_CONFIG.example.json` contains a concrete local E0 CSV configuration
and reviewed SportyBet/CSV club-name equivalences for that competition. Its
relative CSV path assumes installation at the default location above. Adjust
the path when using a different configuration directory.

The existing local CSV was digest-checked against the previously recorded
`705ef4373f6c4c9ec09b67eb3ca3398d6a770813095efc5d629edb591e4ee41c`.
A default runtime configuration was created on this machine using that existing
file, explicit England Premier League identities and **network access disabled**.
It does not guess what Bet9ja's bare “Premier League” means and contains no
ticket-structure attestations. Existing configuration would have been preserved.

Each `football_data_files` entry declares `path`, `division`, `season`,
`competition` and `downloaded_at`. These are caller-supplied provenance, not
facts inferred from a filename. The importer reads a local manually downloaded
CSV; it never fetches the site. Parsing uses the same frozen bytes archived by
the revision. Duplicate fixture imports are rejected instead of silently picking
an observation. Blank scores are not converted into completed matches.

Local observations are used before network retrieval when they already satisfy
supported selections. To enable API-Football, explicitly set `network_enabled`
to `true` and supply `API_FOOTBALL_KEY` in the server environment. Keep keys out
of the configuration and repository. Unsupported dates/seasons have their own
`source_access_restricted` reason. Global source failures stop further successful
retrieval but the served workflow still publishes full-population accounting
using available evidence and explicit retrieval reasons. Unknown schema errors
are not reclassified as scoped access restrictions.

API quotas, retry limits, worker ownership and cancellation/publication fencing
remain in force. UTC-aware kickoff dates use the normalized UTC calendar date
for API query planning; naive values retain their saved calendar date without
an invented timezone.

## Ticket verification

Source scores establish selection outcomes; they do not establish a bookmaker's
ticket structure or special settlement rules. Unknown structures remain
unresolved, even when a selection has a known loss or every selection won.

Reviewed server configuration can now enable a **specific frozen ticket** using:

```json
{
  "provider": "sportybet",
  "row_id": "EXACT_EXTRACT_SHA256:ROW_INDEX",
  "raw_value": "EXACT_ORIGINAL_BET_TYPE",
  "selected_systems": [],
  "structure": "ordinary_accumulator",
  "settlement_policy": "ordinary_regulation_no_promotions",
  "evidence": "Reference to reviewed provider structure and settlement evidence"
}
```

Place such records in `ticket_structures` only after checking the provider
semantics and that original ticket. `single` is also supported and requires
exactly one leg. The provider, original type, selected systems and digest-bound
row must agree; unsupported/system structures are not enabled. Audited ticket
identity, lines and native event IDs cannot be changed to manufacture a match.
No real ticket was marked verified as part of this implementation.

## Extended HTTP contract

The existing five endpoint paths are retained:

- `GET /api/results/<run>/codes?view=saved&offset=0&limit=25` lists original
  retained rows before evaluation. Its `input_digest` can be supplied as
  `snapshot` on later saved-population pages to detect changing checkpoints.
- `POST /api/results/<run>` accepts optional `row_id` in addition to `refresh`
  and `allow_partial`. Omitting it checks all rows. A selector must name a row
  in that exact frozen population; it is not a booking-code lookup.
- `GET /api/results/<run>/codes?revision=<id>&status=won` filters a pinned report.
  `total` stays the population size; `filtered_total` is the filtered count.
  Other filters are lost, void, pending, unresolved and not_checked.
- Add `row_id` to either code-list query to focus one saved row while keeping
  `total` equal to the full-run population. The UI exposes Show all saved codes
  to clear this focus.
- Existing summary, job polling and cancellation endpoints serve the UI.

Different single-code jobs for one run serialize their read/merge/publication
cycle. A single-code refresh fetches dates for that code only, reusing frozen evidence
for previously checked rows. Their checked-row membership is merged only when population and
configuration digests agree. Each revision still contains all retained rows.
Inputs, local CSVs, configuration and available raw source responses are archived
as digest-checked base64 observations in evidence. Published evidence includes
the exact original extract bytes. New API client responses also preserve HTTP
response bytes; older cache entries can only supply the JSON observations they
actually retained. Normalized fixture facts are never presented as recovered
original HTTP bytes.

## Verification and observed coverage

Full suite: **599 tests passed**. The existing unclosed-file ResourceWarning
remains; no tests failed. `git diff --check` passed.

The new automated historical corpus checks one code followed by all codes, all
five outcomes, concurrent single checks, per-date restriction isolation including
warm-cache reuse, local-evidence reuse without constructing a network client,
tamper rejection, structure binding and malformed-row conservation. Full report
replay is compared after removing the CSV, configuration, cache and registry input.
HTTP tests use the actual local transport. The console test executes the real
page script through filtering, revision-pinned pagination and stale navigation.

Browser smoke verification opened the actual 8 October Bet9ja 25-code run and
checked one row offline. All 25 rows stayed in accounting, with 1 checked and
uncertainty displayed. The Bet9ja preview used isolated output storage. A second browser check opened
the published old SportyBet report, displayed CSV source/score evidence and
filtered winners without changing the summary. At a 390px viewport the document
remained 390px wide, with no horizontal overflow. The browser
tool's proxy required a disposable preview-only fetch-site adjustment; production
loopback/Origin/fetch-site protections were unchanged and remain tested.

An offline evaluation of the actual older SportyBet run
`run-sportybet-20261010-071900-651fee94` was also published to the normal local
results store, revision `20261024T201254745047-d791485a`:

| Observation | Count |
| --- | ---: |
| Retained tickets / checked tickets | 25 / 25 |
| Saved selection occurrences | 473 |
| Matched selection occurrences | 114 |
| Distinct matched CSV fixtures | 20 |
| Selection wins / losses / voids / unresolved | 66 / 39 / 9 / 359 |
| Confirmed ticket wins / losses | 0 / 0 |
| Unresolved tickets due to unverified structure | 25 |

An independent direct CSV check confirmed the FT/HT score facts for all 114
matched occurrences across the 20 fixtures. Independently comparing goal totals
against the saved Over/Under picks confirmed 36 selection outcomes, including
integer pushes. This is a bounded check, not a claim that every unsupported
market or provider settlement rule has been verified.

These are outcomes according to the retained CSV evidence, not an independent
certification of the data publisher. The unmatched/unsupported selections and
unverified ticket structures remain visible. **This does not establish that no
historical code would have won.** Wider historical competition coverage and
evidence-backed provider settlement verification remain real data prerequisites,
not reasons to discard saved tickets or fabricate a binary result.

Saved-code cards now wrap their actions at narrow widths and put the kickoff
history note on its own line. Desktop (1454px) and mobile (390px) browser checks
verified that both Check result and Copy remain inside the card. The ten-code
September 29 Bet9ja run was exercised through its direct code and whole-run
actions, including focused result display and conserved run totals.
