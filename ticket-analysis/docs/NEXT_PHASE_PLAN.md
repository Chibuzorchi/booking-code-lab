# Next-Phase Plan — Booking Codes for Both Providers

**Goal (restated, in your words):** *"I need to be able to get the booking code for
both sportybet and bet9ja — that's the goal of this entire process."*

Concretely, the deliverable at the end of this roadmap is: **a short list of booking
codes you can copy straight into each app to stake**, already run through the
concentration/risk engine so the codes are de-correlated and labelled by bucket
(low-odds "bankers" vs high-odds "longshots"). Two providers, two code lists, each
risk-managed.

This document is the map: where we are, and the phases still ahead.

---

## Where we are — "you are here"

```
  HARVEST                 RISK ENGINE (v1)              OUTPUT
  ┌──────────┐            ┌──────────────────┐          ┌───────────────┐
  │ sportybet│ ✅ fresh   │ structured ID    │ ✅        │ concentration │ ✅ (report)
  │  book+scan│──────────▶│ odds semantics   │──────────▶│ report        │
  ├──────────┤            │ coverage-first   │ ✅        ├───────────────┤
  │ bet9ja   │ ✅ fresh   │ snapshot diffs   │ ✅        │ SUBSET SELECT │ ✅ built
  │  book+scan│ (150 codes │ semantic hash    │ ✅        │ → codes to    │   emits stake-
  │           │  validated)│                  │          │   stake        │   ready codes
  └──────────┘            └──────────────────┘          │   stake        │     the codes
                                                         └───────────────┘
```

### Completed phases

- **Phase 0 — Harvesters (both providers).** `book_random` + `scan_code` exist for
  both sportybet and bet9ja. Booking (unstaked coupons) and code-mutation scanning
  both work.
- **Phase 1 — Risk engine v1, offline report (release-001).** Population / A-B split /
  provisional flat-file report.
- **Phase 2 — Correctness pass (validated 2026-09-21).** The six correctness fixes,
  37 tests passing:
  - structured native selection identity (fixed the `as_int() or 0` bug that crushed
    opaque SportyBet event IDs like `sr:match:BFYXxqIWnRhOrMaV|L6iXaA` to `0`);
  - site-displayed odds / parsed-leg product / **verified payout semantics** kept
    strictly separate — payout stays `unknown`, never promoted by assumption;
  - reports **lead with coverage + concentration + shared-leg fraction**, not A/B;
  - snapshot diffs distinguish *confirmed* vs *unverified* disappearance vs
    *identity-format change*;
  - portable semantic hash separated from filesystem provenance hash.
- **Phase 3 — Full fresh SportyBet harvest, proven end-to-end.** Booked fresh seeds →
  scanned Y4QVYJ → **92 qualifying tickets, all 2,550 legs structured-native, 0
  quarantined** → concentration analysis surfaced a real hidden SPOF (the
  "3+ Goals in a Row = No" filler market = ~20% of shared selections; worst single
  leg in 14/92 tickets). **SportyBet is validated: booking → structured harvest →
  concentration, working on fresh data.**

### What that means for the goal
Both providers now support booking, harvesting, analysis, and exposure-constrained
subset selection. Phase 5 is implemented in `portfolio/selection.py` and exposed
through `--select`. The next planned slice is Phase 6: separate output profiles.
Bet9ja dropped-leg detection still requires an immutable earlier snapshot for
comparison; it remains follow-up work.

---

## Remaining phases

Phases 4 and 5 below are completed, with their original specifications retained
for context. Phase 6 is next; Phases 7–8 describe later work.

### Phase 4 — bet9ja parity ✅ DONE (validated 2026-09-21)
**Result:** parser gap found and fixed — bet9ja's feed carried `marketId` and a native
outcome selector (`<E_ID>$<selector>`, e.g. `835737049$S_OU@2.5_O`) but the parser
discarded both, so legs fell back to weak legacy-display identity. `infra/parser.py`
now populates `market_id`, `outcome_id`, and `specifier`. Fresh harvest: booked seed
`5SH7H3B` → scanned → **150 qualifying coupons, 2,084 legs, 100% structured_native,
0 legacy, 0 quarantined** (`scan_5SH7H3B_20260921-134525-328c0ddf8c35.json`). Report:
`reports/bet9ja-harvest-fresh-20260921/`. Concentration: median 12 legs, 40.8% median
shared-leg fraction, worst single leg (Spezia–VIS Pesaro 1X2/1) in 17/150 codes. Note:
bet9ja emits no separate site-displayed odds, so `site_product_discrepancy = 0` and
payout stays `unknown` (correct). Both providers now reach structured-identity parity.
Tests: 37 portfolio + 2 bet9ja live integration, all green.

### Phase 4 (original spec) — bet9ja parity (CRITICAL PATH)
**Why:** the goal explicitly names both providers; bet9ja is the unproven half.
**Do:**
1. Run a full fresh bet9ja harvest (book unstaked seeds → scan → extract), mirroring
   the SportyBet Phase-3 run.
2. Feed the bet9ja extract through `python -m portfolio` and confirm structured
   identity actually populates (native_event_id / market_id / outcome_id non-empty,
   not zeroed) — the same assertion that caught the SportyBet Setka bug.
3. Handle bet9ja-specific gaps the engine hasn't met yet: dropped-leg detection
   requires diffing against an **earlier immutable snapshot** (bet9ja silently drops
   expired legs on re-decode, and `num_legs` is recomputed so a naive count check
   never fires); flag virtuals as unmodellable rather than mixing them into
   concentration.
**Done when:** a fresh bet9ja pool reports the same coverage/concentration block
SportyBet does, with 0 identity-unresolved legs (or every unresolved leg explained).
**Gate:** read-only decode of codes we booked; no staking.

### Phase 5 — Exposure-constrained subset selector ✅ DONE (2026-09-21)
**Delivered:** `portfolio/selection.py` + CLI `--select --max-exposure K [--target N]`.
Given a candidate pool it returns a greedy exposure-constrained set of booking codes such
that **no single selection identity appears in more than K selected codes** — bounding
the blast radius of any one game. Deterministic (most-independent-first greedy: ascending
peak/total leg pool-popularity, then leg count, then id), reproducible, and fully
explainable: every selected code lists which legs it shares and with which codes; every
rejected code names the leg that blocked it and the codes already holding it. Outputs
`selection.json`, `selected_codes.txt` (copy-paste ready), a report section, and prints
the codes. Proven on both providers: bet9ja 116/150 at K=3 (worst leg 17→2), SportyBet
63/92 at K=3; cap verified exact by an independent recompute. Cross-provider keys never
collide, so K is enforced per provider automatically. 8 new tests (45 total green).
Payout stays `unknown`; odds shown are the parsed leg product. Carried forward: flag
bet9ja "Z." virtuals as unmodellable (they currently pass through as normal legs).

### Phase 5 (original spec) — Exposure-constrained subset selector (CRITICAL PATH — this emits the codes)
**Why:** this is *the* step that answers "which booking codes do I actually stake?"
It takes the overlapping pool and returns a de-correlated shortlist, per provider.
**Do:** deterministic selector that, given a pool + a per-selection exposure cap (e.g.
"no single leg may appear in more than K of the chosen tickets"), returns the subset
of booking codes that maximises coverage while respecting the cap. Output = a plain
list of codes + a one-line justification per code (which shared legs it carries).
**Done when:** `python -m portfolio ... --select --max-exposure K` prints a ranked
list of booking codes for a provider, reproducibly.
**Gate:** offline; operates only on already-harvested codes.

### Phase 6 — Two-bucket output profiles (bankers vs longshots)
**Why:** your stated strategy — "an engine that churns out realistic games with low
accumulated odds [and] high chances of winning, and then the random ones with high
odd." Two distinct code lists per provider.
**Do:** run the selector under two profiles — `low-odds` (bankers) and `longshot` —
each de-correlated independently. Crucially: **longshot overlap is not automatically
unimportant** — importance depends on stake exposure, so both buckets get real
concentration limits, not name-based leniency.
**Done when:** each provider emits two labelled code lists.

### Phase 7 — Settlement collection + evidence gate (validation, not a blocker)
**Why:** to know whether the risk-managed codes actually win over time, and to unlock
EV grading. **This does not block producing codes** — it tells us if the codes were
good.
**Do:** poll SportyBet native `isWinning`/`matchStatus` for settled legs; record
settled_net_pnl in `portfolio/positions.py`; keep payout semantics `unknown` until
evidenced (never promoted by assumption). bet9ja settlement needs a placed CID.
**Gate:** read-only polling of existing codes.

### Phase 8 — Probability / EV grading, then generation engine (later)
**Why:** moves from "de-correlate what was harvested" to "propose fresh tickets."
**Do:** attach fair probabilities (devig / real-league priors) to graded legs; then a
construction engine that *generates* candidate tickets to a target odds band and runs
them straight through Phases 5–6. Distinguish **candidate → selected → placed**
throughout.
**Gate:** paper-only; no auto-staking. Stays behind the settlement evidence from
Phase 7.

---

## Critical path to the goal, in one line

**Phase 4 (bet9ja parity) → Phase 5 (subset selector) → Phase 6 (two buckets)** gives
you: for *each* provider, two short lists of booking codes — bankers and longshots —
de-correlated and ready to copy into the app. That is the goal. Phases 7–8 tell you
whether the codes win and let the engine invent new ones.

## Standing constraints (unchanged)
- sportybet and bet9ja stay **separate engines**.
- Live booking is authorised for **unstaked** coupons only.
- The risk engine is **paper-only / read-only**: no real-money auto-staking, no
  external placement, no live in-play.
- Payout semantics remain `unknown` until evidenced — never promoted by assumption.

## Recommended next slice
**Phase 6 — separate output profiles.** Harvesting and subset selection are already
implemented for both providers. The next slice is producing independently selected,
labelled lists for each profile; this is planned work, not part of the directory cleanup.
