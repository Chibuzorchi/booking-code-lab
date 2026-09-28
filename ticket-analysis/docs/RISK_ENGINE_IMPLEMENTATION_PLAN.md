# Sport Portfolio Analysis — Implementation Plan

Date: 2026-09-21
Status: implementation authorized; provisional offline reporting delivered, plus a correctness pass (structured identity, separated odds semantics, coverage/concentration-first reporting) validated on 2026-09-21. Later statistical capabilities retain their evidence gates.

## Delivery amendment — accepted final review

This amendment takes precedence over the original phase ordering below.

- The user intentionally considers and stakes multiple tickets. Concentration analysis leads; the older design's single-ticket fit question is resolved.
- First deliver a provisional flat-file report in the current workspace, before repository migration, SQLite or a full cross-provider resolver. Preserve inputs and a completion manifest; quarantine ambiguous identities.
- Add configurable strategy profiles (for example lower-odds and longshot). These are separate from A/B overlap categories. Report within profiles and across their deduplicated union; never call lower odds guaranteed high probability.
- Distinguish candidate codes, selected tickets and placed positions. Subsequent position tracking must preserve repeated wagers and include previously placed unsettled exposure when reviewing new candidates.
- Begin lossless raw preservation and parser corrections early; defer storage migration until needed. The target monorepo layout remains a target, not a prerequisite for the first report.
- Code mutation changes lookup identifiers, not ticket legs. Measure acquisition bias by seed/session; do not assume adjacent identifiers share selections or that different seeds guarantee independence.
- Add a later construction milestone: assemble proposed tickets from a selection pool under configurable odds/leg/compatibility constraints; separately validate provider booking-code creation. Without validated probabilities this is constraint-based construction, not high-chance generation. Reproducible random sampling must record its seed. Wager placement remains outside scope.
- Evaluate objectives explicitly; longshot overlap is not automatically unimportant. Report gross payouts, stakes, net profit/loss, drawdown and outstanding exposure separately when supported. A future win is not proof of cumulative profitability.

Release sequence: provisional report -> reliable ingestion/identities -> dynamic selection and positions -> storage/packaging as needed plus settlement -> policy/probability research -> ticket construction and booking integration.

### Implementation checkpoint — 2026-09-21

Delivered locally:

- Dependency-free `python3 -m portfolio` CLI for both providers' existing extract shape.
- Dynamic named odds/leg profiles and provider-qualified shortlists, CLI overrides, combined/per-profile provisional A/B and event-exposure reports, R1/R2 and explicit long-odds proxy R3.
- Quarantine of incomplete identities and conflicting code observations; identical-content aliases and separate content/code counts.
- Exclusive output directories, retained input copies, effective policy, hashes and completion manifests; optional previous-report membership/A/B diff.
- Optional user-supplied selected/placed/settled position snapshots, repeated-wager preservation, currency-separated stake exposure, candidate-to-open-position links, and reported settled net P&L. This is not provider-verified settlement or an append-only ledger.
- Both provider clients archive raw observations before parsing/filtering. New exports preserve native selection fields, timestamps, ticket types and SportyBet system metadata.
- SportyBet parser resolves explicit references without first-outcome fallback, preserves repeated-event selections, and rejects ambiguous or duplicate references.
- Scanner exports retain all qualifying tickets; top-N affects the displayed code list only. Effective collection bounds and scan limits are recorded.

Validation: 24 offline unit/integration tests passed, including parser fixtures,
archival, export retention, money arithmetic, report replay and no-overwrite behavior.
The saved SportyBet sample yields 124 classifiable candidates (23 provisional A,
101 B) and two unclassified tickets with missing identities. A 2,000–10,000 odds
view yields 47 candidates (13 A, 34 B). These are descriptive results, not outcome
validation. No live booking, wagering or network probes were performed for this release.

Still pending: verified selection equivalence and cross-provider resolution; source-age
eligibility, automatic watching, richer profile filters, automatic settlement collection
and its evidence gate, historical strategy evaluation, probability models, ticket
construction and provider booking integration. The provisional release does not clear
the later data or statistical gates.

### Correctness checkpoint — 2026-09-21 (identity, odds semantics, coverage)

Second same-day pass, addressing the audit-confirmed defects. Implemented, wired and
tested locally; no scope beyond the six correctness items below.

Delivered:

- **Odds semantics separated (`portfolio/contracts.py`).** Every ticket now carries
  `site_displayed_odds` (with `site_odds_source` provenance), `parsed_leg_product`, and
  `payout_semantics{status: unknown, ...}` as distinct fields. Payout status stays
  `unknown`: agreement between site odds and the leg product, or an input *claiming*
  verification, must not promote either number to a trusted multiplier. Profiles declare
  and enforce their odds **basis**; `ticket_structure` (single/accumulator/system/
  bet_builder) is recorded from declared metadata, not inferred from products.
- **Structured native identity.** Selection identity now keys on
  `native-v1: (provider, native_event_id, market_id, specifier, outcome_id)`, with the
  legacy `display-v1: (provider, event_id, market, pick)` retained as an explicitly
  separate, provisional scheme. Legacy and structured identities are never silently
  equated; each classifiable leg reports its identity mode.
- **Reports lead with population, coverage and concentration.** Output opens with the
  analysed `population` label (harvested pool vs explicit shortlist), a coverage block
  (classifiable fraction, identity-mode leg counts, zero-ID and quarantine reasons,
  site/product discrepancy counts), then concentration (shared-selection blast radius,
  median shared-leg fraction, ticket sizes). A/B is demoted to a labelled navigation aid,
  not a risk grade.
- **Snapshot comparison detects changed selections, not just membership.** Diffs classify
  `confirmed_disappearance_from_complete_response` vs
  `possible_disappearance_incomplete_or_unverified_retrieval` vs
  `identity_format_change_or_unresolved`, so a display->structured re-identification is not
  misreported as a leg vanishing, and incomplete retrieval is not called confirmed loss.
- **Portable semantic hash split from provenance.** `semantic_hash` covers analysis meaning
  and is stable across relocation; `provenance_hash` covers source bytes/filesystem paths.
  Replay in a new directory reproduces the semantic hash while provenance differs.
- **Bounded read-only SportyBet refresh of the known sample codes** (including both Setka
  codes N5PZ3Y, N5PZDF). Timestamped raw bodies and enriched extracts preserved under
  `reports/correctness-refresh-network/`. No new booking, wagering, or code enumeration.

Validation: **37 offline tests pass** (up from 24), adding 11 correctness tests plus a
`tests/fixtures/sportybet_setka_native.json` fixture — covering opaque-ID zero recovery,
specifier/provider false-merge prevention, legacy/structured separation, odds
non-promotion, profile basis enforcement, confirmed-vs-unverified disappearance,
format-change-not-removal, semantic-hash portability, and coverage zero-ID counts.

Key evidence from the refresh:

- **Setka zero-ID was a local conversion bug, now proven and fixed.** SportyBet returns a
  valid non-numeric id (`eventId = "sr:match:BFYXxqIWnRhOrMaV|L6iXaA"`); the prior
  integer coercion crushed it to `0`, which the analyser rejected. The enriched extract now
  preserves `native_event_id` + `market_id`/`outcome_id`/`specifier`, so these legs classify
  as `structured_native` (112 of 118 legs in the refreshed set). Recovery: resolved.
- **The site-vs-product odds discrepancy is time-varying.** For N5PZ2P the site figure was
  1.7x *above* the leg product in the archived observation and ~7x *below* it live
  (product 1.93M -> 24M between fetches while site barely moved). Neither field is a stable
  payout multiplier; keeping `payout_semantics` unknown is the correct posture.

Unresolved / deferred (not regressions):

- Archived (legacy-display) and refreshed (structured-native) extracts cannot be diffed as
  the same identities; this is correctly reported as an identity-format change, so the
  archived->live comparison is a mechanism demonstration, not a real before/after.
- The refresh covers only the known sample codes (authorised scope), so its concentration
  view is degenerate. A real structured-identity concentration picture needs a **full fresh
  harvest**, which belongs to a later, separately authorised slice.
- Structured IDs strengthen identity **within a provider only** — they do not establish
  cross-provider equivalence, retrieval completeness, or payout rules.

Still out of scope here: subset/exposure-constrained recommendation, settlement collection
and its evidence gate, and probability/EV grading.

## 1. Objective and interpretation

Build a local, auditable tool that identifies shared selections and event exposure across Bet9ja and SportyBet booking codes, explains the relationships, and supports human selection decisions. Add historical evaluation and probability estimates only as their respective evidence gates are met.

Concentration reduction is an engineering objective. Improved financial outcomes are a hypothesis. No phase assumes profitability, and an inconclusive or negative research result is valid.

This plan supersedes conflicting recommendations in `RISK_ENGINE_DESIGN.md` for implementation planning. Phase 0 reconciles that draft before it becomes an implementation specification.

### Existing configurable odds controls

Both providers already support `MIN_TOTAL_ODDS`, `MAX_TOTAL_ODDS`, `--min-odds`, `--max-odds-cap`, leg-count limits, and `TOP_N`/`--top`. A maximum of zero currently means no cap. Preserve these capabilities and compatibility during migration.

The audit's concern is the selected policy and descending-odds ranking, not an inability to configure odds. High odds are not automatically evidence of bad value, and low odds are not evidence of good value. Record and evaluate the actual settings instead of treating the observed 2,000 minimum as a permanent product requirement.

Separate three operations:

1. Collection: which codes are queried and which responses are retained.
2. Candidate selection: which retained ticket versions enter an analysis.
3. Presentation: how results are sorted or truncated for display.

Changing analysis settings cannot restore tickets excluded from historical collection. Every run must disclose its collection limits, stopping conditions, and retained population.

## 2. Architecture decisions

Use one repository for first-party code. Keep provider-specific retrieval and parsing independent. Share contracts, storage interfaces, and analysis. Adopt namespaced Python packages so the two existing `engine`, `infra`, and `scripts` packages cannot shadow one another.

Target layout:

```text
sport-portfolio/
  pyproject.toml
  src/sport_portfolio/
    providers/bet9ja/
    providers/sportybet/
    contracts/
    storage/
    candidates/
    analysis/
    settlement/
    pricing/
    cli/
  research/
  tests/fixtures/
  docs/adr/
  configs/
  data/                 # local, ignored by Git
  reports/              # local, ignored by Git
```

Treat `sports-skills` as an optional external dependency pinned to an audited release or commit and accessed through an adapter. Tier 1 must work without it installed. Do not default to a submodule or importing its private `_calcs.py` interface throughout the application. Evaluate its supported public interface first; isolate any unavoidable private integration and test its contract.

The local LICENSE and package metadata say MIT; the README separately describes personal-use expectations and third-party data restrictions. Record software attribution and source-specific data conditions separately. A dependency choice does not establish permission to redistribute source data.

Keep `gamble` as reference material. Import reviewed decisions with provenance, not the entire legacy methodology. No mandatory LightGBM model, blending mechanism, or financial thresholds are inherited automatically.

Use local SQLite for the catalog and append-only records, with raw response files referenced by content hash. Generated JSON reports are the machine-readable interchange format; text/TSV summaries support inspection. Start with CLI commands, not a dashboard.

## 3. Delivery phases and acceptance gates

### Phase 0 — Reconcile the specification and freeze the research questions

Tasks:

- Revise the design's “safe to stack,” “true probability,” and investment-equivalence claims.
- Correct the correlation explanation and remove the library's unvalidated scalar adjustment from authoritative grading.
- Reclassify previous settlement probes as preliminary observations; reopen the obtainability gate.
- Replace “permanently un-backtestable” with explicit current coverage and known limitations.
- Separate exposure, loss probability, expected value, and evidence quality in the output contract.
- Specify verified straight accumulators as the initial supported financial payoff structure. Other ticket types remain visible as unsupported until modeled.
- Record architecture, candidate policy, supported scope, settlement semantics, and validation gates as short ADRs.
- Define hypotheses, comparison baselines, metrics, experiment logging, and the conditions for an inconclusive result before inspecting strategy performance.
- Reconcile `gamble` contradictions: probability fusion is rejected by its accepted ADR; unknown price timestamps must not be assigned a fabricated earlier availability time.

Deliverables: revised design, ADRs, evidence register distinguishing observed/inferred/unverified claims, research protocol.

Gate: every claimed capability has evidence or an explicit unresolved status; no backtest result is required to approve descriptive overlap analysis.

### Phase 1 — Establish the repository and preserve current behavior

Tasks:

- Inventory existing files and nested Git state before moving anything. Preserve existing work and retain a recoverable pre-migration copy or commit once repository initialization is authorized.
- Create the first-party package structure, namespaces, package configuration, and local CI checks.
- Migrate providers independently; retain compatible entry points and environment/CLI settings where practical.
- Keep `sports-skills` optional; avoid copying the third-party working tree into first-party source.
- Ignore raw data, reports, credentials, local environment files, caches, and account-specific artifacts. Commit only reviewed, sanitized fixtures.
- Make display sorting configurable. Descending odds remains an available display option, not a quality score.

Verification: offline parser and settings regressions; both provider packages import in the same process; CLI help and effective configuration preserve min/max odds, zero-cap semantics, and leg limits. Network operations are not part of routine CI.

Gate: packaging changes preserve provider behavior and do not silently change collection scope. Creating or publishing a GitHub repository is a separate delivery action, not part of writing this plan.

### Phase 2 — Make ingestion lossless and identities reliable

Tasks:

- Preserve timestamped raw responses before normalization, with provider, country/site, endpoint identity, response hash, parser version, and collection run ID. Strip credentials from retained request metadata.
- Store provider-native event, market, outcome and specifier identifiers as strings; preserve namespaces and original values.
- Add normalized sport, participants, competition, period, line/handicap, participant-specific target, outcome, and settlement-rule variant where known.
- Iterate actual selected references, preserving multiple selections on one event. Resolve exact market/specifier/outcome combinations. Never silently fall back to the first available outcome.
- Persist ticket type, system configuration, bet-builder structure, unavailable selections, source total odds, computed odds, and their semantics. Do not interpret every displayed total as a straight-accumulator payout multiplier.
- Add schema version, observed time, source timestamp when supplied, kickoff timezone, completeness state, and mapping errors. Never infer historical observation time from a filename alone.
- Persist all successfully retrieved candidates before candidate-policy filtering, within an explicit retention policy. Record rejected candidates and reasons, failures, truncation, scan ordering and stopping conditions. Do not expand network enumeration just to improve archival coverage.
- Version repeated observations of a code. A booking code is a lookup reference, not immutable ticket content. Preserve original selections when a later response drops them.
- Import existing extracts as legacy records with explicit missing-field flags. Re-fetches create new observations and cannot retroactively repair unknown historical state.
- Deduplicate observations without merging separate actual wager positions. Preserve code aliases for identical ticket content.

Verification: fixtures covering repeated-event selections, different goal lines, first-half versus full-time, early payout versus standard markets, missing references, expired legs, code aliases, and unsupported ticket structures. Assert raw-to-normalized selection-count and reference consistency.

Gate: ambiguous selection mappings are quarantined, never reported as verified identities. Existing lossy records remain usable only for clearly labeled provisional analysis.

### Phase 3 — Implement dynamic candidate selection and immutable snapshots

Tasks:

- Ingest folders and provider-qualified code lists into the catalog. Explicit code resolution uses the corresponding provider; ambiguous provider identity is reported.
- Introduce versioned named policies with providers, kickoff window, observation freshness, supported ticket types, sport/market filters, min/max odds, min/max legs, virtual-event handling, and include/exclude lists.
- Define precedence: explicit CLI overrides > named policy > defaults. Emit the resolved policy. Explicit exclusions win over includes; included codes do not bypass identity or supported-type validation. Report included codes that fail eligibility.
- Select the latest admissible observation available at the analysis time. Reject future information during historical replay.
- Keep refresh dynamic and snapshot contents immutable. A refresh creates a new run and recalculates overlap; it never mutates a previous recommendation.
- Persist run time/as-of time, exact ticket-version IDs, policy hash, software/schema versions, exclusions and reasons, and collection coverage warnings.
- Add an optional folder-watch refresh after deterministic one-shot CLI behavior works. Debounce partial files and expose refresh failures.
- Report candidate-set changes: additions, removals, changed ticket content, and resulting A/B transitions.

Illustrative policy fields, not calibrated recommendations:

```yaml
policy_version: 1
providers: [bet9ja, sportybet]
kickoff_window_hours: 48
max_observation_age_minutes: 60
ticket_types: [straight_accumulator]
min_total_odds: null
max_total_odds: null
min_legs: null
max_legs: null
include_codes: []
exclude_codes: []
refresh_on_ingest: true
```

The new policy uses null for an absent bound; adapters translate legacy zero-as-disabled caps. Validate contradictory ranges and disclose that kickoff-based eligibility alone does not guarantee current bookability.

Gate: replaying a snapshot reproduces its candidate membership and analysis; changing odds thresholds changes the current set while preserving earlier runs.

### Phase 4 — Deliver trustworthy descriptive exposure analysis

Tasks:

- Build exact-selection and same-event inverted indices, pairwise shared-selection reports, and connected groups of related tickets. Use indexed joins; avoid an unconditional all-pairs scan.
- Define same-selection identity using verified event, market, period, specifier, target, outcome and rule semantics. Preserve native identity alongside normalized equivalence.
- Add cross-provider event resolution using sport, competition, participant aliases, kickoff, and match context. Store mapping evidence, resolver version, confidence and manual overrides. Ambiguity remains unresolved; team-name similarity alone is insufficient.
- Distinguish cross-provider event matches from selection equivalence. Matching events with different settlement rules do not establish identical bets.
- Report A: no verified exact overlap in this snapshot; B: verified exact overlap; and an explicit unclassified list for inadequate identity/completeness. Attach same-event exposure and cross-provider coverage warnings independently.
- Produce R1 for all selections on at least two distinct ticket contents, R2 as configurable top-N/percentile exposure ranking, and R3 as either a clearly labeled long-odds proxy or later supported probability filter. R2/R3 never silently alter A/B membership.
- Define shared-leg fraction exactly as shared distinct selections divided by total distinct selections. Report maximum ticket-count exposure separately; do not call an unspecified weighted score bounded between zero and one.
- Add optional user-supplied stake positions. Show exposed stake and fraction of supplied budget; otherwise report count exposure only. Missing stakes must not be silently treated as zero.
- For verified straight accumulators, describe the consequence of a losing shared leg. Do not sum overlapping scenario losses as if they were independent.
- Show same-event differing selections as dependency warnings without inventing a correlation coefficient.

Outputs: snapshot manifest, A/B lists, unclassified records, per-code similarity explanation, shared-selection ranking, event-exposure report, and data-quality/coverage report.

Verification: hand-calculated graphs and event mappings, no self-overlap from duplicate rows, stable behavior under input reordering, exact threshold boundaries, unresolved identities, and A/B changes after candidates enter or leave. Use the current 126-ticket sample only as a provisional regression fixture, not ground truth.

Gate: every overlap can be traced to original selections and every classification states its candidate scope and coverage. No probability or profitability claims are required.

### Phase 5 — Collect and validate settlement evidence

This phase can begin once Phase 2 provides stable snapshots; it need not wait for all reporting work.

Tasks:

- Implement a read-only SportyBet settlement adapter first, based on observed schema and retained fixture evidence. Evaluate Bet9ja retrieval paths separately without concluding that a failed path rules out every source.
- Preserve original ticket identities and attach later settlement observations; never replace original legs with a shortened post-event response.
- Model pending, won, lost, void, half-win, half-loss, unresolved, and corrected states where supported. Record source, observation time, finality and payout factor semantics.
- Validate what `isWinning`, status and refund fields mean for each supported case. Treat unknown meanings as unresolved.
- Join Bet9ja real-event results through the resolver where supported. Final scores alone cannot settle corners, player statistics, or early-payout rules; obtain the required evidence or leave unresolved.
- Poll relative to the latest event on each ticket with bounded retries and backoff; support postponements and corrections. A fixed T+2 interval is not a finality guarantee.
- Measure settlement coverage by distinct events, market/rule type, ticket type, age and provider, including missingness and retention duration.
- Start with at least 50 settled ticket snapshots across multiple dates and relevant supported cases as an engineering pilot. This is not a profitability sample-size threshold.

Gate: demonstrate complete original-leg reconciliation and correct payoff calculation for the declared supported cohort; publish unresolved counts and exclusions. Restricted support can pass with explicit limitations. There is no blanket provider-wide pass from one example.

### Phase 6 — Evaluate optional subset recommendations

Tasks:

- Implement a deterministic heuristic enforcing explicit selection/event count caps, optional stake-exposure caps, and ticket/budget limits. Report constraint conflicts rather than silently relaxing them.
- Optimize a documented exposure objective with deterministic tie-breaking. Do not maximize ticket count or distinct games as a substitute for value, and do not force budget consumption.
- Keep this output labeled an exposure-constrained subset, not an optimal or profitable portfolio. Record excluded tickets and reasons.
- Replay policies using frozen decision-time candidate snapshots and supported settlements. Include random same-size subsets, simple event-cap policies, shorter-ticket policies, and an unfiltered reference where comparable. Include unspent funds/no-bet as an explicit outcome.
- Compare using equal starting budget and documented stakes. Separate fixed-size comparisons from policies that change ticket count; stratify or match odds/leg-count distributions where appropriate.
- Measure net return, drawdown, wipeout frequency, largest exposed stake, payout distribution and unresolved-result rate. Report sensitivity to unresolved outcomes instead of dropping them silently.
- Use chronological development/validation/test windows; keep shared-event clusters from leaking across partitions. Use time/event blocks for uncertainty estimates rather than treating repeated legs as independent samples.
- Log every tested configuration, including odds bounds and top-N settings. Freeze the evaluation window and report uncertainty; repeatedly trying settings against the same test set invalidates it as a holdout.
- Determine statistical adequacy from effect size, dependence and payout rarity. Many long-odds tickets may produce insufficient information despite a large ticket count.

Gate: descriptive recommendations pass on correctness and transparency. Claims of reduced realized risk require adequate held-out evidence; claims of positive expected returns require separate evidence. Negative or inconclusive findings do not invalidate the overlap tool.

### Phase 7 — Add coverage-aware probability and EV research

Tasks:

- Audit the pinned `sports-skills` adapter and compare candidate sources by exact market, line, period, rule, available timestamp and outcome completeness. Event/league coverage alone is not pricing coverage.
- Start with a deliberately narrow market cohort such as standard pre-match football 1X2; expand only after measurement. Preserve non-football and unsupported markets in descriptive analysis.
- Archive contemporaneous full mutually exclusive market prices. Do not normalize overlapping double-chance outcomes as if they formed a partition.
- Treat de-vigged prices as market-implied estimates. Benchmark proportional normalization and justified alternatives on validation data, recording model/method versions.
- Distinguish an external-market benchmark from an independent predictive model. Never equate price disagreement with proven exploitable edge. Do not revive arbitrary probability fusion.
- Match contract semantics and price freshness across sources; prediction-market contracts require verified settlement equivalence. Include execution costs or payout adjustments where applicable.
- For supported binary fixed-payout tickets, compute estimated unit-stake EV as `p * decimal_odds - 1`. Use actual payoff distributions for refunds, partial settlements and other structures; do not compare only to a de-vigged benchmark when calculating executable EV.
- Evaluate calibration, Brier/log loss, reliability and uncertainty on held-out events, then ticket estimates where support exists. Hit-rate ordering of three labels is insufficient validation.
- Use product probabilities only under a disclosed and justified independence assumption. Exclude unsupported same-game joint probabilities from authoritative grading; use clearly labeled bounds or scenarios if useful.
- Report probability, estimated EV, concentration, uncertainty and coverage separately. No complete-ticket probability/EV if any required component lacks support.
- Keep Kelly out of v1 recommendations. Any future sizing must handle probability uncertainty and joint portfolio exposure; individual-ticket Kelly values must not be summed as though bets were independent.

Gate: no probability grade is released beyond its validated market cohort. Unknown remains a valid output. Numeric estimates must be reproducible from inputs available at the decision time.

### Phase 8 — Forward paper validation and operational handoff

Tasks:

- Freeze a policy/model version and accumulate future decision snapshots and settlements without retrospective edits.
- Monitor parser drift, missing fields, stale prices, resolver confidence, coverage changes and candidate distribution changes. Degrade affected outputs to unknown rather than silently applying defaults.
- Verify append-only persistence, idempotent imports, backup/restore, source timeouts, rate limits and deterministic offline replay.
- Produce a release report listing supported providers/markets/ticket types, measured limitations, held-out and forward findings, and known failure cases.
- Document CLI workflows for ingest, policy selection, refresh, explain-code, exposure reports, settlement collection and replay. Keep network access explicit and report failed refreshes.

Gate: hand off the descriptive tool once its checks pass; release statistical recommendations only after their additional gates. No automatic wager placement or stake execution is included.

## 4. Dependency sequence and useful stopping points

```text
0 Specification -> 1 Packaging -> 2 Reliable ingestion
                                    |-> 3 Dynamic candidates -> 4 Exposure reports
                                    |-> 5 Settlement evidence
                                          3 + 4 + 5 -> 6 Policy evaluation
                                          2 + 5 -> 7 Probability research
                                          validated components -> 8 Forward validation
```

First useful release: Phases 0–4, supporting both providers for verified descriptive analysis. SportyBet can lead settlement research without excluding Bet9ja from overlap analysis. External pricing and profitable strategies are not prerequisites for this release.

Second release: settlement evidence and reproducible policy evaluation. Third release: validated probability estimates for a restricted cohort, if evidence supports them.

## 5. Deferred work

- System-ticket, bet-builder and promotion-specific payoff support beyond the initial verified cohort: separate supported-contract increments with fixtures.
- Option substitution: separate design after identity, payoff and exposure analysis are reliable. Changing market on the same event may preserve dependence. A suggested replacement must create a new candidate version with updated odds and rules; existing booking codes cannot be assumed editable.
- Additional betting venues, dashboard, distributed services and automated placement: outside this plan's initial delivery scope.

## 6. Evidence and references

Local evidence reviewed:

- `bet9ja/engine/settings.py`, `sportybet/engine/settings.py`: configurable odds and leg limits.
- Both `scripts/scan_code.py` and `engine/scanner.py`: overrides, filtering, descending-odds sorting and truncated persistence.
- `sportybet/infra/parser.py`, `sportybet/engine/coupon.py`: selection mapping and serialization limitations.
- `sports-skills/src/sports_skills/betting/_calcs.py`: proportional de-vigging and heuristic correlation adjustment.
- `sports-skills/LICENSE`, `README.md`, `pyproject.toml`: software license and separate data-use statements.
- `gamble/docs-v1/ADR-003_MODEL_BASELINE.md`, `DATA_AS_OF_POLICY.md`: useful constraints and assumptions requiring reconciliation.

Research used in the preceding audit:

- Clarke, Kovalchik and Ingram, [Adjusting Bookmaker's Odds to Allow for Overround](https://www.sciencepg.com/article/10.11648/10026106): de-vigging methods and normalization limitations.
- Bailey and colleagues, [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): selection effects from repeated historical experimentation.

These references motivate safeguards; they do not validate this project's eventual probabilities or returns.


### bet9ja parity checkpoint — 2026-09-21 (structured identity, fresh harvest)
Brought bet9ja to SportyBet's identity standard. Root cause mirrored the SportyBet
Setka bug: the identity pieces existed in the feed but the parser dropped them.
`bet9ja/infra/parser.py` now extracts `market_id` (`marketId`), `outcome_id` (the
native `<E_ID>$<selector>` token), and `specifier` (`@x.x`). Verified live, then a full
fresh harvest: seed `5SH7H3B` -> 150 qualifying coupons, 2,084 legs, 100%
structured_native, 0 quarantined (scan_5SH7H3B_20260921-134525-328c0ddf8c35.json). Report at
reports/bet9ja-harvest-fresh-20260921/. bet9ja carries no separate site-displayed odds
so site_product_discrepancy is 0 and payout remains unknown. Remaining bet9ja-specific
gap unchanged: dropped-leg detection still requires diffing against an earlier
immutable snapshot. Both providers now validated end-to-end on fresh data.

### Subset-selector checkpoint — 2026-09-21 (Phase 5, the code-emitting step)
Added the deterministic exposure-constrained subset selector: portfolio/selection.py
with CLI --select --max-exposure K [--target N]. It converts an overlapping candidate
pool into the largest de-correlated set of booking codes under a hard per-selection
exposure cap (no selection identity in more than K selected codes), bounding single-game
blast radius. Most-independent-first greedy, deterministic and reproducible, with full
per-code justification (shared legs + which codes) and per-rejection blocking reasons.
Emits selection.json, selected_codes.txt (copy-paste), a report section, and stdout
codes. Validated both providers: bet9ja 116/150 and SportyBet 63/92 at K=3, cap verified
exact by independent recompute; enforced per provider since cross-provider keys never
collide. 8 new tests, 45 total green. Payout remains unknown (odds = parsed leg product).
Open: bet9ja virtuals ("Z." prefix) still pass through as normal legs; flag as
unmodellable in a later pass. Next: Phase 6 two-bucket profiles (bankers vs longshots).
