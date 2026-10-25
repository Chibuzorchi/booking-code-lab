# F9 source feasibility spike — 24 October 2026

Plan step 2 (`F9_RESULTS_PLAN.md` section 10): verify permitted source access, actual
season/league/field coverage and cost, by hand-checking real events. This review
fetches no CSV result data from Football-Data (automated/bot/AI use is restricted
by its own terms) and configures no API-Football account (no credentials exist in
this workspace). It verifies openfootball directly against raw GitHub content and
the GitHub API, not against an AI-summarized fetch, after one summarized fetch was
caught fabricating a result that the raw file does not contain (see "Methodology
note" below). No source is enabled as a result of this review.

## Hand-checked sample

26 distinct events pulled from the two audited runs' real selections
(`run-bet9ja-20261023-065902-28fff68b`, `run-sportybet-20261023-070118-f6753695`),
chosen to span major leagues, lower leagues, cups, a continental qualifier, a
non-European league, women's football, a US college competition, and both the
naive (Bet9ja) and offset-aware (SportyBet) kickoff representations:

| Provider | League (as displayed) | Event | Kickoff (as recorded) |
| --- | --- | --- | --- |
| bet9ja | AFC Challenge League | Al-Ittihad Aleppo - Paro FC | 2026-10-23 15:00:00 |
| bet9ja | CAF Champions League, Qualification | Atletico Petroleos de Luanda - Scottland FC | 2026-10-23 14:30:00 |
| bet9ja | Cymru Championship | Caerau Ely - Llantwit Major | 2026-10-23 18:45:00 |
| bet9ja | EFL Cup | Manchester City - Brighton | 2026-10-28 19:30:00 |
| bet9ja | MLS | CF Montreal - Nashville SC | 2026-10-24 17:30:00 |
| bet9ja | NCAA, Regular Season | Central Connecticut Blue Devils - New Haven Chargers | 2026-10-24 16:00:00 |
| bet9ja | National League | Boreham Wood - Fylde | 2026-10-24 14:00:00 |
| bet9ja | National Womens Soccer League | Denver Summit FC - Racing Louisville FC | 2026-10-24 22:30:00 |
| bet9ja | "Premier League" (displayed) | FC Aktobe - FC Kairat Almaty | 2026-10-25 13:00:00 |
| bet9ja | Primera Division Women | Real Madrid - CD Tenerife | 2026-10-24 14:00:00 |
| bet9ja | Segunda RFEF | Basconia - Club Portugalete | 2026-10-25 16:30:00 |
| bet9ja | Serie C | Catania - Giugliano | 2026-10-25 14:00:00 |
| bet9ja | UEFA Champions League | AEK Athens - Real Madrid | 2026-11-04 17:45:00 |
| sportybet | England · Premier League | Arsenal - Everton | 2026-10-24T14:00:00+00:00 |
| sportybet | England · National League | Eastleigh FC - Boston United FC | 2026-10-24T11:30:00+00:00 |
| sportybet | Wales · Cymru Premier | Connah's Quay Nomads FC - Llandudno FC | 2026-10-24T11:30:00+00:00 |
| sportybet | Intl Clubs · UEFA Champions League | Atletico Madrid - Bayern Munich | 2026-11-03T20:00:00+00:00 |
| sportybet | Egypt · 2. Division A | Haras El Hodood - Proxy SC | 2026-10-23T12:30:00+00:00 |
| sportybet | Brazil · Brasileiro Serie B | AC Goianiense GO - Gremio Novorizontino SP | 2026-10-24T19:30:00+00:00 |
| sportybet | USA · MLS | New York Red Bulls - Inter Miami CF | 2026-10-24T21:30:00+00:00 |
| sportybet | Spain · LaLiga 2 | Leganes - Mallorca | 2026-10-24T14:15:00+00:00 |
| sportybet | England · EFL Cup | Bradford City FC - Peterborough United | 2026-10-27T19:45:00+00:00 |
| sportybet | Russia · MFL, Division A | FK Nizhny Novgorod - FK Zenit St. Petersburg | 2026-10-23T12:00:00+00:00 |
| sportybet | Germany · DFB Pokal | Rot-Weiss Essen - RB Leipzig | 2026-10-27T17:00:00+00:00 |
| sportybet | International · Liga ABA | Igokea Aleksandrovac - KK Crvena zvezda Belgrade | 2026-10-24T16:00:00+00:00 (basketball) |
| sportybet | Australia · A-League, Women | Western Sydney Wanderers - Brisbane Roar FC | 2026-10-24T03:15:00+00:00 |

"Premier League" on Bet9ja's own label here is Kazakhstan's top flight
(FC Aktobe, FC Kairat Almaty), not England — a concrete instance of F9-06's
warning that a league label alone is not safe fixture-mapping evidence.

## openfootball — verified against raw content, not a summary

GitHub API listing of `openfootball/football.json`'s `2026-27` folder (ground truth,
not model-summarized):

```
de.1.json  en.1.json  en.2.json  es.1.json  fr.1.json  it.1.json  nl.1.json  pt.1.json
```

Repo root also lists a non-seasonal `2026/` folder containing only `br.1.json`
(Brasileiro Série A, calendar-year season). This is the *entire* file set; the
repo has never published cups, English tiers below the Championship, women's
leagues, NCAA, CAF/AFC/continental qualifiers, or any Brazilian tier other than
Série A, in this folder or any other season folder checked. Of the 26 hand-checked
events, only one league (England · Premier League) is in scope at all, and the
Kazakhstan "Premier League" fixture is explicitly out of scope despite the matching
label. The Brasileiro Série B fixture is out of scope because only Série A exists.

Fetching `2026-27/en.1.json` directly (raw bytes, parsed in Python, not summarized):

- 380 scheduled matches; only 50 carry a `score` key at all; the rest have no
  `score` field, not a null or zero placeholder.
- Every scored match's `date` is `2026-09-20` or earlier. Every match from
  `2026-10-10` (Matchday 6) onward, including "today's" (2026-10-24) fixtures,
  has no `score` key — the file's actual update point is roughly five weeks
  behind the session's current date, not "yesterday" or "today".
- `2026/br.1.json` (Brasileiro Série A, not in our sample but checked for a second
  freshness data point) shows the identical cutoff: last scored date `2026-09-20`,
  103 of 380 matches still unscored. Two unrelated competitions lagging to the
  same exact date is strong evidence of one shared generation/update point for
  this dataset's current refresh, not per-competition staleness.
- The specific match in our sample, Arsenal FC vs Everton FC on `2026-10-24`
  (`"time": "15:00"`, no zone marker), exists in the file with no `score` key.
  15:00 with no zone, for an English fixture before the last Sunday of October
  (when BST ends), is consistent with SportyBet's independently-recorded
  `2026-10-24T14:00:00+00:00` (15:00 BST = 14:00 UTC) — one corroborating data
  point for "openfootball's bare time is the fixture's local kickoff time", not
  a general rule, and not proof for leagues checked zero times.

Verdict: even for the one in-scope league, openfootball cannot presently supply
a same-week result for this population; its freshness lag (~5 weeks as observed)
exceeds the useful horizon for a run evaluated the week of the match. 25 of 26
hand-checked events are permanently out of scope regardless of freshness, because
their competitions are not and have never been published by this source.

## Football-Data — not fetched; usage-restricted

Already established in `F9_PLAN_QA.md` and re-confirmed this session: the data
page's own terms state intended use is "private individuals only, **NOT**
commercial or data training products using automated bots/scrapers/AI." This
review did not download any Football-Data CSV (match-result) file, consistent
with the plan's existing gate: this source stays manual-file-import only
(`allow_partial`-style user-supplied snapshot with provenance), never an
automated fetch inside `sources/`. Its documented division list (England tiers
1-4 + non-league "Conference", Scotland, the five other major European leagues,
plus ~16 "extra" countries) would, on paper, cover more of our sample than
openfootball — EFL Cup, DFB Pokal, continental qualifiers, NCAA, NWSL and
women's leagues are still not covered by any documented Football-Data division.
Real per-division file freshness cannot be verified without triggering the same
restriction this gate exists to respect, so it stays unverified and disabled
pending a manually-provided file.

## API-Football — credentials configured, verified against the live API

**Update (24 October 2026, after this spike's original write-up):** the user
signed up directly with api-sports.io (not RapidAPI, not the unrelated
apifootball.com), added a free-tier key as `API_FOOTBALL_KEY` in the
repo-parent `Sport/.env` (never committed, never logged), and one real,
quota-conscious call was made with `ApiFootballClient.get_fixtures_by_date()`
to confirm it actually works:

- `get_fixtures_by_date("2026-10-24")` with no `league`/`season` filter
  returned 1,666 real fixtures across all competitions for that date,
  including finished matches with real scores (`status.short == "FT"`,
  e.g. Chicago Red Stars W 0-1 Houston Dash W).
- The free tier does **not** allow combining a specific `league` filter with
  `season=2026` (its season coverage for that combination is restricted to
  2022-2024); passing `league`/`season` together with the current season
  fails. The workaround already matches the client's existing contract:
  query by date only, with no league/season params, and filter down to the
  8 in-scope leagues in our own code after the fetch -- never via the API's
  query params. No code change was needed in `sources/api_football.py` for
  this; `extract_regulation_score()`'s existing FT-only acceptance already
  parsed this real response correctly.
- One request consumed, out of the 100/day free-tier quota.

Its documented free tier (100 requests/day) and the distinction between
fixture/event/statistics capabilities and settlement rules were already
recorded in `F9_PLAN_QA.md`. This source is now unblocked for step 5's
adapter work, subject to the date-only query workaround above and the
100 requests/day budget that step 5's "budgets/cache" requirement must
actually enforce.

## Methodology note: do not trust an AI-summarized fetch as evidence

The first check of `en.1.json` used a fetch tool that retrieves a page and has a
separate model summarize it. Asked for the Arsenal-Everton match's score, it
returned a confident, specific, and entirely fabricated result ("2-1, HT 1-0"),
plus a false total ("145 of 380 matches scored") and a false claim that scores
ran through the file's end. Fetching the same URL's raw bytes and parsing the
JSON directly showed zero score fields past 2026-09-20 and no score at all for
that match. This is the exact failure mode section 4/8 of the plan already
guards against for provider sources ("missing facts remain null, not zero/false";
"raw evidence actually used"): it applies equally to how this spike — and the
eventual `sources/` adapters — must read evidence. No adapter may sit an
AI-summarization step between a fetched response and a recorded fact; adapters
parse raw bytes with a versioned parser, full stop.

## Requests/quota used this session

- openfootball: 2 GitHub API calls (directory listings) + 2 raw file fetches
  (`en.1.json`, `br.1.json`), all public, unauthenticated, no quota consumed.
- Football-Data: one page fetch of public documentation (`data.php`), no CSV/data
  download; one `notes.txt` request redirected (302) and not followed further,
  to avoid any appearance of automated data retrieval beyond documentation review.
- API-Football: zero requests at the time this spike was originally written
  (no account existed then). One real request was made afterward, once the
  user configured a key, as recorded in the update above -- see that section
  for the count against the 100/day free-tier budget.

## Revised source-strategy recommendation

The originally proposed chain (Football-Data primary, openfootball fallback,
API-Football last resort) is not demonstrated feasible for this population as
configured today:

1. openfootball alone resolves at most 1 of 26 hand-checked events by
   competition scope, and that one is not actually resolvable right now because
   of observed multi-week staleness. Keep it enabled only for its 8 fixed
   top-two-tier European leagues, and only after a freshness check per job, not
   as a default fallback expected to cover gaps.
2. Football-Data needs a manual-file-import path before it supplies anything;
   there is no automated variant to enable under its current terms. Build the
   user-supplied-CSV-with-provenance path before counting on this source at all.
3. API-Football is the only source with a plausible path to lower leagues, cups
   and continental qualifiers. It is now credential-verified against the live
   API (see the update above): a date-only query (no league/season filter)
   returns real fixtures with real scores. Its free-tier quota (100/day) still
   will not cover 102+62 tickets' worth of fixtures in one pass, and the
   league+season=2026 filter restriction still applies -- step 5's budget/cache
   work must batch by date, filter leagues in our own code, and spread requests
   across days or fall back cleanly when the daily quota is exhausted, not
   assume one unbounded pass.
4. At least 6 of the 26 sampled competitions (AFC Challenge League, CAF
   Champions League Qualification, NCAA, NWSL, Primera Division Women,
   A-League Women) have no confirmed path to a result source in this review at
   all. Per section 6's registry, these must be recorded `unsupported_sport` /
   `unsupported_market`-equivalent at the competition level, not silently
   retried against sources that have never been shown to carry them.

Step 3 (versioned mappings/rules) should proceed only for the openfootball-scoped
top-flight leagues as a first, narrow, honestly-labeled slice, while the
Football-Data manual-import path and an API-Football account decision are
raised with the user as separate, explicit asks before any broader rule work
is built on an assumption of coverage this spike did not find.
