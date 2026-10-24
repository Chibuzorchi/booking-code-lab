# F9 plan QA — 24 October 2026

Verdict: the original plan was not ready to implement as a reliable ticket win-rate
checker. It described a useful direction, but left correctness contracts undefined.
The revised `F9_RESULTS_PLAN.md` specifies those contracts and the evidence gates
below. A specified gate is not evidence that the underlying capability works.
This review implements no result-fetching or settlement backend.

Reviewed baseline: `cd05900`, original F9 sections 1–6. Evidence: both 23 October
committed run extracts, their provider parsers/models, registry/API/server,
`contracts.py`, `positions.py`, `.gitignore`, the proposed sports-skills functions,
and primary source documentation linked below. No external account was created,
paid service enabled, or provider result claimed.

## Local evidence

| Observation | Bet9ja / 23 Oct | SportyBet / 23 Oct |
| --- | ---: | ---: |
| Saved ticket snapshots | 102 | 62 |
| Selection occurrences | 2,491 | 1,789 |
| Unique provider event IDs within the run | 826 | 605 |
| Distinct displayed market labels | 51 | 206 |
| Tickets containing a non-football selection | 2 | 4 |
| Kickoffs without a timezone | 2,491 | 0 |
| Raw response paths present and files found | 102 | 62 |

Bet9ja sports: 2,464 Soccer selections, 21 American Football, six Tennis.
SportyBet sports: 1,706 Football, 62 Basketball, 21 Handball. Labels such as
`Over/Under` are therefore not sufficient to choose a football goal rule.
Counts are within each run; native IDs from different providers are not a shared
cross-provider event namespace.

Input digests (SHA-256 of committed extract bytes):

- Bet9ja: `4d21f7bb1ee34ee7f4279869423e7d84040ff63e98b64f64ec28d85aeee55784`.
- SportyBet: `876a20eda53859b2f39eac3cfc1b192beaaec1ade4bed615a65cb0824de4c5ee`.

Bet9ja ticket types are the strings `0` (100 tickets), `1` (one), and `2` (one).
SportyBet has `multiple` (43), `MULTIPLE` (three), `SINGLE` (one), and missing types
(15). These are observations, not validated mappings to payout structures.
`contracts.py` deliberately keeps payout semantics unknown.

All 164 snapshots include native event/market/outcome fields and observation times.
The plan's statement that only displayed names exist was inaccurate: IDs exist,
but a verified mapping to the result source is missing.

Directly executing the proposed sports-skills name matcher returned `True` for
`Arsenal` / `Arsenal Women`, `Chelsea` / `Chelsea U21`, and `Inter` / `Inter Miami`.
The openfootball normalizer also supplies a zero score when a score is missing
and appends `Z` to a supplied time without proving its timezone. These helpers
are unsafe to copy unchanged into settlement.

## Findings and required disposition

P0 = can yield a false outcome or misleading main percentage.
P1 = can prevent delivery, corrupt history, or materially bias interpretation.

| ID | Priority | Gap in original plan | Required correction in revised plan |
| --- | --- | --- | --- |
| F9-01 | P0 | No exact population or denominator; “pool” can mean current eligibility or distinct subset | Hash-verified retained snapshots; explicit full-population counts, duplicate policy and complete/partial label |
| F9-02 | P0 | “Every code generated” exceeds what scanners save | Label results as retained history; retain alias/checkpoint-loss caveats and separate forward capture work |
| F9-03 | P0 | Football-only sources applied to mixed sports | Sport-gated market dispatch; unsupported sports remain in denominator as unresolved |
| F9-04 | P0 | Type strings/numeric codes treated as accumulators without validation | Versioned provider type mappings; separate selection success proxy from a ticket win |
| F9-05 | P0 | Naive Bet9ja times implicitly treated as UTC | Preserve original time; verify source timezone before exact-time matching; date-only evidence remains date-only |
| F9-06 | P0 | Fuzzy reuse accepts different squads/clubs | Preserve women/youth/reserve qualifiers; competition-aware ordered team mapping, ambiguity rejection and negative examples |
| F9-07 | P0 | Seven market families are presented as covering every observed market | Inventory all sport/market/pick/specifier combinations; capability registry, exact semantics, explicit unsupported reasons |
| F9-08 | P0 | Final score assumed sufficient for all rules | Separate regulation/extra-time/penalties, periods, statistics, timelines and provider promotion rules |
| F9-09 | P0 | Any unmatched leg forces ticket unknown even when a valid losing leg settles an accumulator | Explicit precedence for supported structures; aggregate only after provider rules apply |
| F9-10 | P0 | Pending, missing, stale, disputed, postponed and void conflated | Orthogonal fixture/matching/data/rule states, conservative settlement and retryability |
| F9-11 | P0 | Free primary source assumed usable for automated retrieval | Review actual published usage restrictions; configured permitted source or manual-file import, not assumed automation rights |
| F9-12 | P1 | “Static file” interpreted as no freshness/quota concerns | Dated source capability matrix; detect delayed upstream updates, 429s, unavailable seasons and incomplete responses |
| F9-13 | P0 | First source wins even when stale, conflicting, or missing required fields | Capability-aware fallback, field provenance, conflict quarantine; missing facts never become false or zero |
| F9-14 | P1 | One synchronous POST fetches an entire run with no durable job contract | Separate resumable result jobs; idempotency, per-run serialization, bounded retries and retained last good report |
| F9-15 | P1 | No cache invalidation, report revision or replay contract | Immutable inputs/responses, content hashes, rules/matcher versions, explicit refresh and report revision history |
| F9-16 | P1 | `ticket-analysis/results/` source package is ignored by `**/results/` | Use `portfolio/results_tracking/`; runtime reports remain under ignored run directories |
| F9-17 | P0 | Leg rate, ticket rate, league rate and return can be mixed | Explicit ticket versus leg denominators; overlapping strata labelled; no inferred payout/ROI or independence |
| F9-18 | P1 | “Regret list” assumes no supplied position means never played; positions normalize expired tickets | Read-only links to exact supplied snapshots, unknown actual-play status, no ledger settlement writes |
| F9-19 | P1 | Attractive prototype competition hides actual coverage gaps | Audit all 164 tickets first; stratified positive/negative fixtures and full report including every unresolved row |
| F9-20 | P1 | UI deferred but no concrete way to inspect proof/results | Specify first CLI artifact and API output; later UI reads the same report and keeps expired historical rows visible |

## Source checks

- Football-Data publishes FT/HT scores and selected match statistics where
  available. Its data page restricts intended usage to private individuals and
  includes restrictions concerning commercial/data-training uses and automated
  bots/scrapers/AI. The plan cannot treat availability as permission for this
  particular integration. This review makes no legal determination.
  [Data page](https://www.football-data.co.uk/data.php),
  [field definitions](https://www.football-data.co.uk/notes.txt).
- openfootball is CC0/public-domain data, not the MIT attribution asserted in
  the original source description. Its generated files update from upstream
  datasets whose updates are not themselves automatic. Daily generation is not
  proof that yesterday's scores are present. [README](https://github.com/openfootball/football.json),
  [license](https://github.com/openfootball/football.json/blob/master/LICENSE.md).
- API-Football's free offering currently lists 100 requests/day and season
  restrictions. Verify the actual account's league/season/field access before
  planning coverage. [Pricing](https://www.api-football.com/pricing).
- API-Football distinguishes regulation, extra time, penalties, and non-final
  fixture statuses. Its fixture/event/statistic capabilities are not bookmaker
  settlement rules. [Official guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide),
  [documentation](https://api-sports.io/documentation/football/v3).

## Evidence still required before outcome claims

1. Verified timezone handling for historical Bet9ja payloads and source timestamps.
2. Audited provider ticket-type mappings and each enabled market's rule semantics,
   including period, line, voids and promotions.
3. Actual source access, season coverage, update latency and field completeness
   for sampled real events, with hand-checked evidence.
4. A matching corpus that rejects ambiguous teams and records every source mapping.
5. A report whose counts conserve the full retained population, whose percentages
   expose pending/unresolved outcomes, and whose revisions replay without network.

The revised plan allows bounded feasibility work to collect this evidence first.
It does not claim the 164 tickets have been settled or that full coverage is
possible from the currently proposed sources.
