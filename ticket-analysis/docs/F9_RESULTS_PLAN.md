# Results tracking — scope (F9)

Status: DRAFT FOR REVIEW (do not build yet).
Scope: for every code a run found (played or not), find out whether it would have
won, so we can see what we skipped and whether this method's odds/league/market
choices are actually working. This is retrospective analysis, not settlement of
real stakes — `positions.json` already tracks what was actually placed; this is
about everything else in the pool.

Historical input requirement (confirmed 24 October): use the retained run's full
`qualifying` snapshots, including started/expired tickets, for retrospective
outcome checks. The current-eligibility `build_pool()` deliberately excludes
started tickets and must not define the outcome-tracking population. Stage 1 now
offers **Show expired codes** for reviewing those historical snapshots; it does
not determine results. Preserve original legs when matching results rather than
replacing them with a newly decoded coupon that may have dropped started legs.

## 1. Where results come from (and where they don't)

Licensed sportsbooks buy settlement/odds data from a handful of B2B vendors —
Sportradar, Genius Sports/BetGenius, LSports — under paid contracts, consumed
server-side. That feed is not public, and there is no confirmation either
provider in this repo uses any specific one. Treating their site or app as an
unofficial results API (reverse-engineering an internal endpoint) is a terms-of-
service problem, not just a technical one — this is exactly the theory behind
the Swish Analytics v. OddsJam suit (2025) over scraped odds data. This plan
does not do that. Flashscore, Sofascore, and LiveScore were checked for a public
API on that basis and ruled out: all three only have unofficial scrapers hitting
a private internal feed, the same problem this plan avoids for the sportsbooks
themselves.

Decision for F9: a layered fallback chain of legitimate/documented sources,
tried in this order per leg, so a gap in one source falls through to the next
rather than the whole leg going unresolved:

1. **football-data.co.uk** (primary) — free historical CSVs, major European
   leagues, no key required, long-standing and widely used by the betting-
   research community. Chosen as primary over football-data.org because it's
   CSV/bulk-download rather than a rate-limited JSON API, which suits fetching
   a whole run's worth of fixtures at once rather than one call per leg.
2. **openfootball / football.json** (fallback 1) — open-data git repo of
   season results as JSON, MIT-style license, no key, no rate limit (it's a
   static file fetch). Covers a different/overlapping league set than (1).
3. **API-Football free tier** (fallback 2, last resort) — broader league
   coverage (incl. lower/foreign leagues bet9ja/SportyBet actually book), but
   a free-tier call budget, so only spent on legs (1) and (2) couldn't resolve.
4. **GOAL API / Highlightly** (fallback 3, unverified) — turned up in research
   as possible broader-coverage vendors but their terms/reliability are not yet
   confirmed; treat as speculative until vetted by hand against a few known
   results, not wired in until then.

Any leg no source in the chain can resolve is **unresolvable**, not wrong — it
must be reported as "no result source," never guessed. That ceiling is accepted
for F9; swapping or reordering a source later only touches `fetch.py`'s source
list, not the matcher or settlement engine.

## 2. The real bottleneck: matching, not fetching

A scanned leg has no canonical event ID — only what the provider displayed:

    {"event": "Team A v Team B", "league": "English Premier League",
     "kickoff": "2026-10-24T15:00", "market": "1X2", "pick": "1", "odds": 2.1}

(see `portfolio/analysis.py` leg shape, used throughout `uniqueness.py`,
`selection.py`, `reporting.py`.) Every source in the chain above has its own
team names, IDs, and competition codes. There is no shared key. Matching means:
competition name -> source competition code, team-name normalization (accents,
"FC", "Utd" vs "United", club vs city name) for both sides, and a kickoff-time
window (same UTC date, some tolerance either side) to disambiguate same-fixture
reruns or postponed/rescheduled matches. This is the part that can quietly be
wrong (false match -> wrong settlement) and needs the most test coverage, not
the HTTP call.

A leg that doesn't confidently match must be reported as **unmatched**, never
guessed into a result. No silent best-effort fuzzy match without a confidence
floor.

### Prior art worth reusing (sports-skills)

The sibling repo `../sports-skills` (public, MIT, `machina-sports/sports-skills`)
already solves pieces of this exact problem for its own ESPN+openfootball
football skill. Its league/result coverage doesn't cover our chain directly
(ESPN + openfootball + understat/fpl, not football-data.co.uk or API-Football),
so it is not a dependency we should pull in — but five pieces of its
`src/sports_skills/football/_connector.py` are worth adapting into our own
`results/` module rather than re-deriving by trial and error:

- `_teams_match` / `_normalize_name` / `_ABBREV` (lines ~1056-1135) — the fuzzy
  team-name matcher. Strips accents/suffixes ("FC", "SC", "AFC"), expands known
  abbreviations ("Man" -> "Manchester", "Spurs" -> "Tottenham"), then matches on
  substring or word-overlap. This is a direct, working answer to the matching
  bottleneck above — our `match.py` should start from this shape, with our own
  abbreviation table for whatever bet9ja/SportyBet display.
- `_RateLimiter` (line ~356) — a token-bucket limiter, one instance per source.
  Directly applicable to API-Football's free-tier budget.
- `_cache_get` / `_cache_set` (lines ~329-355) — a TTL in-memory cache keyed by
  request. Matches our own need (per §3 `cache.py`) to fetch a competition's
  fixtures once per run, not once per leg.
- `_http_fetch` (line ~408) — shared retry/backoff HTTP helper for transient
  errors (429/500/502/503/504). Reusable as-is for any of our JSON/CSV sources.
- `_normalize_openfootball_match` (line ~879) — since openfootball is fallback
  1 in our chain too, this normalizer (deriving win/draw/loss directly from the
  final score, tagging a `"source"` field) is close to a direct port for that
  one source.
- `LEAGUES` dict shape (line ~23) — canonical slug -> per-source identifiers.
  The pattern (not the data) is the right model for mapping bet9ja/SportyBet's
  displayed league strings to each source's own competition codes.

Separately, `sports-skills` also ships a `metadata` skill wrapping TheSportsDB's
free API — but that's team logos/photos/stadium info, not match results, so it
doesn't help settlement and isn't part of this chain. Its README also lists
API-Football as "Coming Soon" (unimplemented there), confirming there's no
existing client to borrow for that fallback — ours would be a fresh adapter.

## 3. Architecture

New top-level package, same layout convention as `portfolio/`:

    ticket-analysis/results/
      __init__.py
      fetch.py       # one adapter per source in the §1 chain, tried in order per leg
      match.py       # leg -> source fixture, or None + reason (adapts _teams_match)
      settle.py      # (fixture result, market, pick) -> won / lost / void / unknown
      cache.py       # on-disk cache of fetched fixtures by competition+date range
                      #   (so a whole run's pool isn't refetched leg by leg)
      __main__.py     # CLI entry point for phase 1 (see §5)

Settlement rules table (one entry per market the scanner records — decision:
build all markets now, not just 1X2/O-U/BTTS):

| market family      | needs from fixture result          |
|---------------------|-------------------------------------|
| 1X2                 | full-time score                     |
| double chance       | full-time score                     |
| over/under goals    | full-time total goals               |
| BTTS                | both teams scored y/n                |
| correct score       | exact full-time score               |
| handicap            | full-time score + line               |
| half-time markets   | half-time score (not available from every source in the chain — must degrade to "unknown", not guess) |

Each rule is a pure function of (final score, market, pick) -> outcome. This
table is the highest-risk part to get wrong silently, since a bad rule looks
identical to a correct one until someone checks a specific code by hand —
needs a fixture of known real results checked by hand before trusting it on a
whole run.

API surface (added to the existing local API in `portfolio/api.py`, same
loopback-only, JSON-only contract as everything else there):

    POST /api/results/<run_id>   -> fetch+match+settle every code in that run's pool,
                                     cached per competition/date so re-asking is free
    GET  /api/results/<run_id>   -> the last computed report (no refetch)

Report shape: per code, per leg: matched (bool), outcome if matched, source
used if matched, reason if not; per code: would_have_won (bool | null if any
leg unmatched); run-level rollup: hit rate by odds band, by league, by market —
the actual "is this method working" answer the user asked for. Separately: for
codes with a recorded position (`positions.json`), cross-check against what
was actually staked, so this never contradicts F6's stake tracking.

## 4. UI (after the backend above exists — separate follow-up, not part of F9)

A fourth console stage, gated the same way stage 2/3 are: "Results" button,
locked until stage 1 has a committed run. Shows won/lost/unmatched counts,
the regret list (codes with no recorded position that would have won), and
the hit-rate rollup. Paginated the same way stage 1/2 lists now are.

## 5. Phased build order

1. `results/fetch.py` + `results/cache.py` against football-data.co.uk for one
   known competition and date range — confirm the client and cache work.
2. `results/match.py` against a handful of real legs from one past run, by
   hand, printed to stdout via `results/__main__.py` — eyeball every
   match/no-match before anything else is built on top of it, since this is
   where a silent wrong answer would hide. Adapt `_teams_match` here rather
   than writing a matcher from scratch.
3. `results/settle.py` with the full market table from §3, each rule checked
   against at least one hand-verified real result per market family.
4. Add openfootball as fallback 1 in `fetch.py` (adapting
   `_normalize_openfootball_match`), re-run step 2's hand-checked legs to
   confirm the unmatched bucket shrinks, not just moves.
5. Wire `/api/results/<run_id>` into `portfolio/api.py`, reusing one run's
   already-committed extract.
6. Console stage 4 (separate follow-up). API-Football (fallback 2) and
   GOAL/Highlightly (fallback 3, pending vetting) are added later, behind the
   same `fetch.py` interface, only if the unmatched bucket after steps 1-4 is
   still too large to be useful.

## 6. Open risks, carried forward rather than hidden

- Even a 4-source chain may leave a large "unmatched" bucket for bet9ja/
  SportyBet's lower-tier leagues; the rollup must report that bucket's size
  prominently, not bury it in an average.
- No proof-of-concept script preceded this plan (explicit choice for F9) —
  step 2 above is where matching quality first becomes visible; if the real
  match rate turns out too low to be useful even after all four sources, that
  is the point to reconsider the chain before sinking more time into the
  settlement table.
- Settlement correctness cannot be unit-tested into confidence alone; each
  market rule needs at least one hand-checked real fixture, recorded in the
  test fixtures the way `test_checkpoint.py` etc. already do for this repo.
- GOAL API / Highlightly are unverified vendors (research found them cited as
  options, not confirmed as legitimate/documented APIs) — must be checked by
  hand against known results before being wired in at all, not just before
  being trusted.
