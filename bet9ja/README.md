# bet9ja bet-booking & coupon scanner

Playwright + Python. Books a random game to get a **seed booking code**, then
mutates the trailing characters and decodes each variant through bet9ja's coupon
API to discover other already-booked coupons — filtering for **bigger total
odds** — without booking each one by hand.

Structure follows the `automation_newgen` convention: **environment separated
from code/test logic**.

## Layout

```
engine/                  # settings, coupon models, scanning, logging, raw archives
infra/
  config/settings.py     # provider configuration loaded from .env
  parser.py              # decode native coupon responses
  clients/coupon_api.py  # GetBookABetCouponV2 client
pages/                   # Playwright page objects
  sports_page.py         # open site/soccer/league, pick a random fixture + odd (dynamic)
  betslip_page.py        # Book a Bet, read code from modal, clear slip, load code, read total odds
tests/
  data.py                # ALL DOM selectors live here
  test_book_and_scan.py  # e2e + API-only scan tests
scripts/
  scan_code.py           # decode/scan a code — API only, no browser
  book_random.py         # book a random game via browser, print the code
conftest.py              # fixtures: settings, coupon_api, scanner, page
.env.example             # copy to .env and tweak
```

## Setup

Run these commands from `Sport/bet9ja/` (`cd bet9ja` from the workspace root).

```bash
make install                 # pip deps + chromium
cp .env.example .env         # then edit as needed
```

## Use

**Primary flow (API-only, no browser):** book a code by hand on bet9ja, put it in
`.env` as `SEED_CODE=...` (or pass it explicitly), then scan. The browser booking
under *Optional* below also works (verified end-to-end against the live site); the
API-only flow is simply faster for day-to-day use.

```bash
# Seed comes from SEED_CODE in .env — no code needed on the CLI:
make scan
python -m scripts.scan_code                     # same thing
python -m scripts.scan_code 5S2HFVg --scan      # or pass a code explicitly (wins over .env)
pytest tests/test_book_and_scan.py::test_scan_from_seed -s   # uses SEED_CODE too
```

**Two commands, one config:** `make api` never opens a browser (decode/scan a code);
`make ui` drives the browser to book a fresh code. Both read a single sectioned
`.env` (SHARED / API PATH / UI PATH). A second file (`.env.<name>`, via `--env`)
is reserved for a different provider or account (e.g. `.env.sportybet`), not for
UI-vs-API — the scanner block is shared (a UI run can book *then* scan).


```bash
# Decode one booking code (fast, no browser):
make decode CODE=5S2HFVg

# Scan variants of a code for higher-odds coupons:
make scan CODE=5S2HFVg
python -m scripts.scan_code 5S2HFVg --scan --min-odds 50 --depth 2

# UI path (browser) — book a random game, print its code:
make ui                      # HEADLESS=false make ui  to watch
make ui-scan                 # book then scan the seed

# Choose how many trailing chars to edit (progressive: 1 -> 2 -> 3):
make scan-1 CODE=5S2HFVg          # edit last char only        (61 candidates)
make scan-2 CODE=5S2HFVg          # last char, then last two   (~3.8k candidates)
make scan-3 CODE=5S2HFVg          # last one, two, then three  (~238k, capped by MAX_CODES_TO_TRY)
make scan   CODE=5S2HFVg DEPTH=2 MIN_ODDS=5000   # any target with overrides

# Tests:
make test                    # full e2e (browser + scan)
make test-scan CODE=5S2HFVg  # API-only scan test
make test DEPTH=2 MIN_ODDS=5000        # pytest dispatch args, like automation_newgen's --env
pytest tests/test_book_and_scan.py::test_scan_from_seed -s --seed-code=5S2HFVg --depth=1
```

Qualifying coupons are filtered using the configured bounds and saved under `results/`:

```
results/
  codes/     scan_<seed>_<ts>.txt    # just the booking codes, one per line, best first — paste-ready
  extracts/  scan_<seed>_<ts>.json   # all qualifying coupons, including every leg
  raw/                             # archived API observations before filtering
```

The **codes** folder contains the displayed shortlist; `TOP_N` limits that list.
The **extracts** folder retains all qualifying coupons and feeds the implemented
[ticket-analysis project](../ticket-analysis/README.md), which compares shared
selections and produces exposure-constrained shortlists for both providers.

## How the scan works

- The code space is **case-sensitive base62**; `5S2HFVg` != `5S2HFVQ`.
- Mutation is **progressive**: vary the last 1 char, then the last 2, then 3
  (`MAX_MUTATION_DEPTH`). In practice the last-char neighbours of a real code are
  densely valid, so stage 1 alone finds many coupons.
- Most far-off codes return `{"R":"ERROR"}`; the scan caps calls with
  `MAX_CODES_TO_TRY` and stops after `MAX_QUALIFYING` hits, with a polite
  `REQUEST_DELAY_MS` between calls.
- Coupons are deduped by their exact selection set, so the same coupon reached by
  two codes is only kept once.

## Coupon API response — key meanings

`GET {COUPON_API_BASE}/GetBookABetCouponV2?couponCode=<code>&v_cache_version=<ver>`

| Key | Meaning |
|-----|---------|
| `R` | `"OK"` if the coupon exists, else `"ERROR"` |
| `D` | data payload (present when `R == "OK"`) |
| `D.BTYPE` | coupon/bet-type code (internal enum — **not** the leg count) |
| `D.DATE` / `D.DATEUTC` | when the coupon was created |
| `D.STAKE` | stake on the coupon (0 if only booked, not staked) |
| `D.BOOKABET_COUPONID` | bet9ja's internal numeric coupon id |
| `D.O` | map of selections, keyed `"<E_ID>$<MARKET>_<SIGN>"` |
| `O[].E_ID` | event (match) id — unique per fixture |
| `O[].E_C` | short event code |
| `O[].E_NAME` | `"Home - Away"` |
| `O[].GN` / `GID` | league name / id (e.g. `Premier League` / `170880`) |
| `O[].SGID` | sub-group id (country/competition; matches sidebar `sg-*` toggles) |
| `O[].SPORT_ID` / `sportName` | `1` == Soccer / human name |
| `O[].M_NAME` | market: `1X2`, `Over/Under`, `Double Chance`, ... |
| `O[].SGN` | picked sign: `1` \| `X` \| `2` \| `Over ` \| `Under ` \| `1X` ... |
| `O[].V` | odds (price) for this selection — **multiply all `V` = total odds** |
| `O[].marketId` / `signId` | internal market/sign ids |
| `O[].isBetBuilder` | bet-builder flag |
| `O[].STARTDATE` / `UTC` | kickoff time |
| `O[].*TransKey` | translation keys resolved against `D.TRANS` |
| `D.TRANS` | dict resolving the `*TransKey` values to display strings |

**Total odds = product of every selection's `V`.** Verified: `5S2HFVg` -> 44.52.

## SportyBet and shared analysis

The separate [SportyBet project](../sportybet/README.md) provides its own client,
parser, and browser pages. Run each provider from its own directory because their
Python packages share names such as `engine` and `infra`. Both export JSON extracts
for [ticket analysis](../ticket-analysis/README.md).

## Reading results

Start with [the results guide](results/START_HERE.txt), then choose the
[browser report](results/index.html) or a per-scan plain-text summary under
`results/summaries/`. New scanner saves regenerate both formats automatically.
Browser controls search, sort, and export historical candidates; they do not
apply the analysis project's exposure-constrained selection policy.

Original `codes/`, `extracts/`, and `raw/` files are preserved. If report generation
fails, the scanner keeps those saved files and logs a warning. To rebuild manually,
run from `Sport/ticket-analysis/`:

```sh
python3 -m portfolio.reporting --provider bet9ja --results ../bet9ja/results
```

## Simulation exclusion

Browser fixture selection skips `Z.` team prefixes, standalone `SRL` markers,
and explicit simulated/simulation/virtual labels (case-insensitive). Scans check
event, league, and sport labels and exclude the whole coupon if any leg matches;
individual legs are never removed from a booking code. New extracts record
`excluded_simulations` and the applied `simulation_filter`. Raw responses remain
archived, and historical extracts and reports are unchanged.

This is label-based detection: unlabelled simulations cannot be identified by this
rule. SportyBet skips outcomes whose fixture row cannot be inspected.
