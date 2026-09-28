# Sport Portfolio Risk Engine — Design v1

> **Status:** DRAFT for review. Documentation only. No implementation begins until
> this doc is accepted and the **Backtest Feasibility Gate** (§9) is cleared.

> **Superseded:** implementation has been authorized under
> `RISK_ENGINE_IMPLEMENTATION_PLAN.md` and its accepted final-review amendment.
> This older draft is historical context, not the implementation specification.
> The user confirmed multi-ticket use, resolving §12.1. Its probability,
> correlation, mutation-neighbour and settlement claims are not accepted evidence.
>
> **Date:** 2026-09-21 · **Owner:** chinonso
>
> This document is the canonical specification for the next phase: turning a pile of
> harvested booking codes into a **risk-managed portfolio of tickets** instead of a
> pile of correlated gambles.

---

## 1. Purpose & Philosophy

Today we harvest booking codes from bet9ja and sportybet by mutating a freshly-booked
seed and keeping the coupons that clear an odds threshold. That gives us *quantity*.
It does **not** give us *quality*, and it silently creates a hidden danger:

> Many harvested tickets share the **same** underlying selection (e.g. "Everton to win").
> If that one game loses, **every ticket containing it loses together.** That is a
> **single point of failure (SPOF)** — undiversified concentration risk.

The mental model is a **portfolio**, exactly like equities:

| Stock world | Our world |
|---|---|
| A share | A single selection (one game + market + pick) |
| A fund / basket | A booking code (a multi-leg ticket) |
| Sector concentration | Many tickets leaning on the same game |
| Idiosyncratic risk | One leg's own chance of losing |
| Systematic risk | A leg shared across many tickets (correlated failure) |
| High/Med/Low risk rating | Per-ticket EV + probability grade |

**What is and isn't in our control.** We cannot control whether a game wins. We *can*
control (a) how **concentrated** our tickets are on the same games, and (b) whether the
games we back are **+EV** (priced better than their fair chance). This engine measures
and manages exactly those two things.

**Guiding principle (borrowed from `/gamble` docs-v1):** paper-first, discipline-first.
The engine **reports and recommends**; the human makes the business decision. No
automatic staking, no auto-placing bets in v1.

---

## 2. Scope

### 2.1 In scope (v1)
- **Local-first, offline analysis.** Runs on the machine, reads harvested extract JSON.
- **Two providers only: bet9ja and sportybet.** These are our primary point of action.
- **Tier 1 — Concentration engine:** find shared selections across a candidate set of
  tickets, quantify blast radius, split codes into *unique* vs *overlapping*, and
  produce a similarity report so the human can decide.
- **Tier 2 — Risk grading:** for legs in *real* leagues, estimate fair probability and
  EV using the vendored `sports-skills` math, and assign a High/Med/Low risk tier.
- **Backtest harness:** validate, on *settled* historical coupons, that the metrics we
  compute actually correspond to real correlated outcomes before we trust them.

### 2.2 Explicitly NOT in scope (v1) — non-negotiable
- No real-money auto-staking or auto-placement.
- No third-party betting sites beyond bet9ja/sportybet (Betfair/Polymarket/Kalshi as a
  *betting venue*). We may *read* Kalshi/Polymarket **prices** as a probability signal in
  Tier 2, but we never place there in v1.
- No live in-play feeds, no live lineups/injuries.
- No web dashboard / Telegram / multi-user service.
- No option-substitution auto-editing of tickets (that is Tier 3 — see §8, future).

These exclusions mirror the `/gamble` v1 invariants and keep us honest.

---

## 3. Repo Structure — Recommendation (decision requested)

**The question:** three repos (`bet9ja`, `sportybet`, `sports-skills`), or something else?

### 3.1 Recommendation: **one monorepo, four packages**

```
sport-portfolio/                     # single GitHub repo
├── harvesters/
│   ├── bet9ja/                       # existing engine, moved as-is (base62, drops expired)
│   └── sportybet/                    # existing engine, moved as-is (base36, freshness filter)
├── risk-engine/                      # NEW — provider-agnostic analysis (Tier 1 + Tier 2)
│   ├── schema/                       # the normalized Ticket/Selection contract (§4)
│   ├── concentration/                # Tier 1
│   ├── grading/                      # Tier 2 (imports vendor/sports-skills math)
│   └── backtest/                     # §9 harness
├── vendor/
│   └── sports-skills/                # third-party, git submodule (personal-use license)
├── shared/                           # logger, env, http helpers used by all packages
└── docs/                             # this design doc + ADRs
```

### 3.2 Why monorepo (rationale)

1. **The shared contract is the whole point.** The risk engine consumes tickets from
   *both* harvesters through **one normalized schema** (§4). In a monorepo, a schema
   change + both producers + the consumer land in **one atomic commit**. In three repos,
   every schema tweak becomes a cross-repo version dance.
2. **Separate engines ≠ separate repos.** Your firm verdict that bet9ja and sportybet are
   *separate engines* (because sportybet serves expired codes and bet9ja drops them) is
   about **engine logic**, not repository boundaries. They stay fully independent packages
   inside `harvesters/` — no shared harvesting core — and that verdict is preserved.
3. **Solo-dev velocity.** One clone, one CI pipeline, one issues board, one place to run
   `pytest`. Cross-cutting refactors (e.g. add `region` to a selection) are trivial.
4. **`sports-skills` stays at arm's length.** It is third-party and **personal-use-only**
   licensed. Vendoring it as a **git submodule** under `vendor/` keeps its boundary and
   license explicit while letting `risk-engine/grading` import its pure math.

### 3.3 Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Monorepo (4 pkgs)** ✅ | Atomic schema changes; one CI; fast iteration; clear vendor boundary | Single repo grows large; must be disciplined about package independence | **Recommended** |
| B. 3 separate repos (bet9ja, sportybet, sports-skills) + risk-engine as 4th | Max isolation; independent release cadence | Schema drift hell; 4 clones; cross-repo PRs painful for a solo dev | Rejected for v1 |
| C. 2 repos: `harvesters` (mono of bet9ja+sportybet) + `risk-engine` | Keeps producers together, consumer separate | Still cross-repo schema coordination between the two | Possible v2 split |

**Migration note:** current code already lives under `/Users/chinonso/Sport/{bet9ja,sportybet,sports-skills}`.
Adopting Option A means moving those into `harvesters/` and `vendor/` and adding
`risk-engine/`. Low risk; no engine logic changes.

> **DECISION PENDING:** confirm Option A (monorepo) or pick an alternative. Everything
> below assumes Option A but is structurally independent of the choice.

---

## 4. Data Model — the Normalized Ticket Schema

Both harvesters already emit an `extracts/*.json` with per-leg detail. The risk engine
needs **one** shape regardless of provider. This is the contract.

### 4.1 `Selection` (the atomic unit — "a share")

```jsonc
{
  "provider":   "bet9ja" | "sportybet",
  "event_id":   "838573129",           // provider event id (stable within provider)
  "event":      "Everton - Ipswich Town",
  "home":       "Everton",             // parsed where available
  "away":       "Ipswich Town",
  "league":     "Premier League",
  "region":     "England",             // country/region (already derived in tagger)
  "market":     "1X2",                 // normalized market family (see §4.3)
  "pick":       "Home",                // normalized outcome label
  "odds":       1.68,                  // decimal odds for THIS leg
  "kickoff":    "2026-09-19T16:30:00Z"
}
```

**Selection identity (fingerprint).** Two legs are "the same selection" iff they match on
a canonical key. We define **two** keys because SPOF has two flavours:

- **`same_pick_key = (provider, event_id, market, pick)`** — identical bet (Everton to win
  on the exact same market). Losing this kills every ticket that holds it. This is the
  **hard SPOF**.
- **`same_game_key = (provider, event_id)`** — same *match*, possibly different market
  (Everton win vs. Over 2.5 in Everton's game). These are **correlated but not identical**;
  they can fail together (Everton gets battered 0-3: "Everton win" loses AND "Over 2.5"
  may too). This is the **soft SPOF**.

Cross-provider same-game matching (Everton on bet9ja == Everton on sportybet) is a
**stretch goal** (§8) — it needs a team-name/kickoff resolver. v1 keeps SPOF within a
single provider.

### 4.2 `Ticket` (a booking code — "a basket")

```jsonc
{
  "code":          "5S6XC7K",
  "provider":      "bet9ja",
  "num_legs":      10,
  "total_odds":    5026.96,            // site/system-aware where provided
  "computed_odds": 5026.96,            // product of legs
  "selections":    [ Selection, ... ],
  "harvested_at":  "2026-09-19T09:53:06Z",
  "seed":          "5S6XC7K"           // the code we mutated to find it
}
```

### 4.3 Market normalization (small, extensible table)

Providers name markets differently ("1X2" vs "Double Chance" vs "Over/Under Goals").
Tier 1 only needs an **exact** match on the raw market for the hard SPOF, so v1 can start
with raw strings. A normalization map (`raw_market → market_family`) is introduced when
Tier 2 needs to devig (it must know a game's full outcome set). Kept as a data file so
new markets are a one-line edit.

---

## 5. Tier 1 — Concentration / Correlation Engine

**Input:** a *candidate set* of tickets — NOT the whole raw harvest, but the shortlist the
human is considering playing (a folder of codes, or a list). **Output:** the split into
unique vs overlapping, plus a similarity report and blast-radius ranking.

### 5.1 Core structures

1. **Selection index:** `same_pick_key → [ticket codes that hold it]`.
2. **Game index:** `same_game_key → [ticket codes that touch that game]`.
3. **Overlap graph:** nodes = tickets; an edge (A,B) is weighted by how many selections
   A and B share (via `same_pick_key`). Two tickets with a heavy edge are near-duplicates.

### 5.2 The three SPOF reports (all produced; user asked for all three)

- **R1 — Shared by ≥2 tickets (strict).** Every `same_pick_key` held by 2+ candidate
  tickets, listed with its **blast radius** = number of tickets it would sink.
- **R2 — Top overlaps only.** The worst offenders — top-N (or top decile) selections by
  blast radius — so attention goes to the real concentration, not the long tail.
- **R3 — Shared AND low-probability.** From R1, keep only selections that are *also* risky
  on their own (long odds now; low fair-prob once Tier 2 is wired). These are the
  double-jeopardy legs: shared *and* likely to miss. **Needs Tier 2 for the prob half;
  until then R3 uses odds as a proxy (odds ≥ threshold = "risky").**

### 5.3 The two output categories (user's primary ask)

Every candidate code is placed in exactly one bucket:

- **Category A — UNIQUE:** none of its selections appear in any *other* candidate ticket.
  Fully idiosyncratic — safe to stack without correlating your book.
- **Category B — OVERLAPPING:** shares ≥1 selection with another candidate ticket. For
  each Category-B code, the report spells out **which** codes it overlaps and **which
  exact legs** are shared:

  ```
  5S6XCQY  (OVERLAPPING)
    ├─ shares "Everton win (1X2)"      with  5S6XCX6, 5S6XCD6      (blast radius 3)
    └─ shares "Over 2.5 — Bayern"      with  5S6XCLK               (blast radius 2)
  ```

  That is the "this code has similar options with this, that, and that code" view you
  asked for — the human then decides: drop it, keep it, or (Tier 3) swap the shared leg.

### 5.4 Per-ticket concentration score

`concentration_score(ticket) = (# legs shared with ≥1 other candidate) / num_legs`,
weighted by each shared leg's blast radius. 0.0 = fully unique; 1.0 = every leg is shared.
Used to rank Category-B codes worst-first.

### 5.5 Diversified subset recommender (optional, same tier)

Given N candidate tickets and a cap `max_tickets_per_selection` (e.g. "no single game may
appear in more than 2 of my played tickets"), select the largest subset that respects the
cap while maximizing distinct games. Greedy first (sort by concentration_score, drop
worst offenders until caps satisfied); exact ILP is a later refinement. This turns
"here are your overlaps" into "here is a clean book to actually play."

### 5.6 What Tier 1 deliberately does NOT do
- It does not judge whether a leg is *good* — only whether it is *shared*. Quality is Tier 2.
- It does not cross providers (bet9ja Everton vs sportybet Everton) in v1.

---

## 6. Tier 2 — Probability & EV Risk Grading

Turns "shared or not" into "smart or not". Reuses `vendor/sports-skills/betting/_calcs.py`
(pure stdlib): `devig`, `find_edge`, `kelly_criterion`, `parlay_analysis`.

### 6.1 Fair probability per leg
1. Pull the game's **full market** odds (all outcomes) from a public source
   (ESPN via sports-skills for real leagues).
2. `devig(...)` → **fair probability** of our pick (strips bookmaker margin).
3. Optionally cross-check against a **prediction-market price** (Kalshi/Polymarket) for
   big matches → a second probability estimate; blend (probability fusion, per `/gamble`).

### 6.2 Per-leg edge
`find_edge(fair_prob, market_prob=our leg's implied prob)` → edge %, EV per $1, Kelly.
A leg where our book's price is *worse* than fair is a **-EV trap** — flag it.

### 6.3 Per-ticket grade
`parlay_analysis(legs=[fair_probs], parlay_odds=total_odds, correlation=…)` →
combined fair probability, ticket EV, ticket Kelly. Then bucket:

| Tier | Rule (indicative — calibrated in backtest) | Meaning |
|---|---|---|
| **LOW risk** | combined fair prob ≥ X% **and** ticket EV ≥ 0 | Realistic, priced fairly-or-better |
| **MED risk** | positive EV but low hit-prob, OR small negative EV | Lottery-ish but not stupid |
| **HIGH risk** | strongly -EV, or hit-prob below floor, or > L legs | The 40-leg mega-piles |

### 6.4 Coverage honesty (critical)
Much of the bet9ja harvest is **virtual / "Zoom" / "Simulated Reality League"** football —
these have **no real-world probability source**. The engine must **flag these legs
`unmodellable`** and refuse to fake a grade. A ticket with any unmodellable leg gets a
`partial` or `ungraded` tag, never a false-confidence number. This is a feature, not a gap:
it tells you which tickets are analyzable at all.

### 6.5 Correlation feeds back into Tier 1
`parlay_analysis` already takes a `correlation` term. Same-game legs (soft SPOF from §4.1)
get a positive correlation, correctly lowering a ticket's true combined probability. This
is where Tier 1's game-index and Tier 2's math meet.

---

## 7. Output & UX

All outputs are **files** (local-first), human-readable, git-diffable:

```
risk-engine/reports/<run-stamp>/
├── category_A_unique.txt         # code | legs | odds        (safe-to-stack)
├── category_B_overlapping.txt    # code | legs | odds | overlaps-with…
├── spof_R1_shared.tsv            # selection | blast_radius | tickets
├── spof_R2_top.tsv               # worst offenders only
├── spof_R3_shared_lowprob.tsv    # double-jeopardy legs
├── similarity_report.txt         # the per-code "shares X with A,B,C" view (§5.3)
├── graded_tickets.tsv            # (Tier 2) code | tier | combined_prob | EV | kelly | coverage
└── recommended_book.txt          # (§5.5) a de-correlated subset to actually play
```

Console prints a summary; the files are the artifact you probe.

---

## 8. Future phases (documented, not built in v1)

- **Tier 3 — Option substitution engine.** For a shared/-EV leg, propose an alternative:
  a different market on the same game, or a different game entirely, that lowers
  correlation and/or improves EV while keeping the ticket's odds band. This is the
  "engine that can change the options" idea. Big; needs a candidate-selection pool.
- **Cross-provider same-game resolver.** Match bet9ja Everton ↔ sportybet Everton by
  team-name normalization + kickoff proximity, so SPOF spans both books.
- **External venues.** Read-only market data first (Kalshi/Polymarket), and only *much*
  later — and only behind explicit gates — any real placement (per `/gamble` philosophy).

---

## 9. Backtest / Feasibility Gate (MUST pass before Tier 1 build)

The user's requirement: *"back test what we've documented to ensure it's obtainable and
the result is achievable."* We do **not** build until we prove the data exists and the
metrics mean something. Two questions, two tests:

### 9.1 Q1 — Can we even get the data? (obtainability) — **LIVE-TESTED 2026-09-21**

Results of the live obtainability probe:

- **sportybet — ✅ FULLY OBTAINABLE, natively.** Re-fetching a settled code (WPB4DA,
  Atletico–Real) via the *same* share endpoint we harvest with returns, per leg:
  `markets[].outcomes[].isWinning` (boolean), plus `setScore` ("2:1"), `matchStatus`
  ("Ended"), `status` (4), `probability`, `voidProbability`, `refundFactor`. **No external
  dependency, no auth.** Ground-truth per-leg win/lose is available today. Coverage:
  **99% real leagues** (measured across 2,970 harvested legs; 1 virtual).

- **bet9ja — ⚠️ NOT natively obtainable for *unplaced* codes.** Re-decoding a settled
  booking code (5S2HFVg) via `GetBookABetCouponV2` **drops the settled legs** rather than
  annotating them (came back with 2 of its original 6, no result fields on any selection).
  The `GetCouponDetailsV2` settlement fields (`betState` Winning/Losing etc.) that the won
  ticket shows require a **placed-coupon CID**, which harvested codes do not have.
  → bet9ja ground truth needs an **external results feed keyed by event id**, which only
  exists for **real** leagues. bet9ja's large **virtual/"Zoom"/SRL** fraction is
  **permanently un-backtestable externally** (consistent with §6.4).

- **`sports-skills` as external source — ◐ REACHABLE, needs a resolver.** ESPN responds
  (`get_daily_schedule` returns a `{date, events}` structure, status True) and the rich
  commands exist (`get_event_summary`, `get_event_xg`, `get_season_standings`,
  `normalize_odds`, plus Understat xG / FPL / Transfermarkt / openfootball results). BUT
  calls require correct `competition_id`/`season_id`/`event_id` plumbing (`season_id="eng.1"`
  was rejected — ids come from `get_competitions`), and mapping our providers' team
  names/kickoffs → ESPN events needs a **name/id resolver**. That resolver is the real cost
  of Tier-2 fair-prob **and** of any bet9ja real-league results.

**Gate 9.1 verdict:** **PASS for sportybet** (native, self-contained ground truth, 99%
real-league coverage) — backtestable immediately. **CONDITIONAL for bet9ja** — gated on
building the ESPN name/id resolver, and even then only for its real-league subset.

**Consequence for sequencing:** the backtest and Tier-1 validation run **sportybet-first**
(zero external integration), and bet9ja joins once the resolver exists. See revised §10.

### 9.2 Q2 — Do the metrics predict reality? (achievability)
Using settled coupons with known outcomes:
- **Concentration test:** take historical candidate sets; confirm that when a
  high-blast-radius selection lost, it did in fact sink all the tickets the engine said it
  would. (This is almost tautological but validates the plumbing end-to-end.)
- **Diversification test:** compare the *variance of returns* of a raw harvested set vs.
  the engine's `recommended_book` subset over several settled days. Hypothesis: the
  de-correlated subset has **fewer total-wipeout days** for the same expected hit-rate.
- **Grade calibration (Tier 2):** bucket settled tickets by predicted tier
  (LOW/MED/HIGH); check realized hit-rates line up (LOW should hit more often than HIGH).
  Adjust the §6.3 thresholds from this data — they are placeholders until calibrated.

**Gate 9.2 passes if:** diversified subsets measurably reduce wipeout frequency, and Tier-2
tiers rank-order realized hit-rates in the right direction on held-out settled data.

### 9.3 Backtest data pipeline
1. Harvest as usual → store codes + extract JSON (already done daily).
2. **After settlement** (T+2 days), poll each code's settlement API → append
   `outcome` per leg + `ticket_result` to a parallel `settled/*.json`.
3. Backtest harness reads harvest+settled pairs → computes the tests above → writes a
   `backtest_report.md`. **Only when that report clears both gates do we start Tier 1.**

---

## 10. Milestones

Revised after the §9.1 live probe — **sportybet leads because its ground truth is native.**

| # | Milestone | Gate |
|---|---|---|
| M0 | **This design doc accepted** + repo structure decided (§3) | Review |
| M1 | **sportybet** settlement poller → `settled/*.json` (uses native `isWinning`) | 9.1 ✅ done |
| M2 | Backtest harness on **sportybet only** + `backtest_report.md` | **9.2 (hard gate)** |
| M3 | Tier 1 concentration engine (schema, indices, R1–R3, A/B split, similarity report) | — |
| M4 | Diversified subset recommender | — |
| M5 | Tier 2 grading on sportybet real leagues (devig/EV/Kelly, coverage-honest tiers) | Calibrated on M2 data |
| M6 | **ESPN name/id resolver** → unlocks bet9ja real-league results + fair-prob | New sub-gate |
| M7 | bet9ja joins backtest + grading (real leagues only; virtuals stay Tier-1-only) | 9.1 conditional |
| M8 | (future) Tier 3 substitution, cross-provider resolver | Separate design |

**Key sequencing insight from the probe:** sportybet is a *complete, self-contained
backtest environment* — we can prove or kill the whole concentration/diversification
thesis on it **without writing a single line of external-API integration**. bet9ja's
external-results dependency (M6) is deferred so it never blocks validating the core idea.

---

## 11. Open questions for review

1. **Repo structure (§3):** confirm monorepo Option A, or choose otherwise.
2. **Candidate set definition (§5):** how do you hand the engine "the tickets I'm
   considering"? A folder? A hand-curated `shortlist.txt` of codes? (Proposed: both — a
   `--from <folder>` and a `--codes a,b,c`.)
3. **SPOF default (§5.2):** which report drives the A/B split by default — R1 (any shared)?
   Proposed default: **R1 defines Category B**; R2/R3 are lenses on top.
4. **Risk-tier thresholds (§6.3):** accept that these are placeholders until the backtest
   calibrates them?
5. **Settlement delay (§9.3):** T+2 days OK for the backtest cadence, or poll sooner?
6. **Virtual/Zoom leagues (§6.4):** confirm we *exclude* them from grading rather than
   guess. (They remain fully usable in Tier 1 concentration analysis.)

---

## 12. Reservations & Unresolved Fit Questions

Honest critique of *this* design, recorded before build so we probe it rather than
discover it at M5. Ranked by how much each would change what we build.

### 12.1 Tier 1 assumes a multi-ticket book — do we actually stake one? **(blocking)**
Concentration/SPOF/blast-radius (§5) only mean something when several tickets are staked
**together**. If Everton sits in 8 staked tickets and loses, 8 die together — real risk,
real value. But the reference win was **one** 10-leg ticket (₦1.5M). For a single ticket,
"shared leg across tickets" is undefined — there is only one basket, so §5.3–5.5 produce
nothing.

The design is therefore quietly built for a **grinder staking a portfolio of 10–50
tickets**, not a **lottery player firing one fat longshot**. Those are different users with
different first tools:
- One ticket at a time → **Tier 2 first** (is *this* ticket +EV / how likely), Tier 1 is
  future work.
- A book of many tickets → **Tier 1 first** (de-correlate the book), as currently
  sequenced.

**Unresolved:** which are you? This answer re-sequences the milestones. Until it's settled,
§10 leading with Tier 1 may be backwards.

### 12.2 Diversification trims the fat tail you're chasing
Even with a multi-ticket book, Tier 1's job is to **lower variance** (fewer total-wipeout
days). But a 5,000-odds 10-legger is a *variance-seeking* play — the fat right tail is the
whole point. De-correlating trims wipeout days **and** mega-score days together. That's a
win for a +EV grinder; it works against "it's luck but I like the variety." Not a math flaw
— a **fit** flaw. Named here so we don't build a seatbelt for a user who came for the speed.

### 12.3 Tier 2 will honestly grade almost everything HIGH / −EV
Bookmaker margin **compounds per leg** — precisely why books love accumulators. `find_edge`
may flag the odd +EV single leg, but a *10-leg parlay being +EV overall* is close to
nonexistent. So Tier 2's truthful output converges on "don't play accumulators" —
simultaneously correct and useless. **Decision needed:** Tier 2's real job is *ranking your
longshots least-bad against each other*, **not** finding a +EV parlay that doesn't exist.
The §6.3 tiers should be framed as *relative* grades within the longshot universe, not an
absolute +EV screen.

### 12.4 The real-league resolver is thin for exactly the leagues you like
Tier 2 fair-prob leans on ESPN (+ optional Kalshi/Polymarket). There is **no** prediction
market for J2 / Indonesian Liga 1, and ESPN coverage of them is thin-to-absent. So a large
share of your *preferred real* legs will still be `unmodellable`, not just the bet9ja
virtuals of §6.4. The dark zone is bigger than §6.4 implies. Coverage must be **measured
and reported per run**, never assumed.

### 12.5 Mutation-harvested codes are a biased population for concentration analysis
We harvest by mutating a seed's last characters, so neighbours share most legs **by
construction**. Tier 1 on a *raw harvest* shows gigantic "concentration" that is an artifact
of the harvest method, not a real book. §5.1 already says the input is a **human shortlist**,
not the raw harvest — but §9.2's diversification backtest needs an **independently-chosen**
ticket population, and mutation-neighbours aren't that. This weakens the statistical power
of the diversification test more than §9 admits. **Mitigation:** the backtest candidate sets
must be assembled from distinct seeds / distinct days, not one seed's neighbourhood.

### 12.6 Repo migration at M0 is premature churn
Build `risk-engine/` reading the **existing** `extracts/*.json` in place. Do **not** move
bet9ja/sportybet into `harvesters/` until the thesis is validated (post-M2) — restructuring
an unproven idea is wasted motion. **Amendment to §3:** treat Option A as the *target*
layout, adopt after M2, not at M0.

### 12.7 The `correlation` term (§6.5) is a guessed knob
Parlay EV is sensitive to it and we have **no data source** for "same-game legs → 0.3." It
is a placeholder exactly like the §6.3 tier thresholds, and should be labelled as one until
(if ever) it can be fit from settled data.

### 12.8 What is *not* a reservation
The sportybet-first, backtest-gated spine is right; M1's native `isWinning` ground truth is
solid and already flowing (fresh sportybet booking landed codes on 2026-09-18). The
feasibility gate (§9) and coverage-honesty (§6.4) are the strongest parts of the doc and
stand.

> **Net:** nothing fatal, but **§12.1 is blocking** — resolve "one ticket vs. a book" before
> committing §10's ordering. Everything else is a framing/labelling correction, not a
> redesign.

---

*End of Design v1. Nothing here is built yet — this is the thing we probe before we write
a line of engine code.*
