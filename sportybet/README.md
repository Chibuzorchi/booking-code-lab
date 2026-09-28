# SportyBet booking and scanning

Run commands from `Sport/sportybet/` (`cd sportybet` from the workspace root).
This project decodes share codes, scans code variants, and optionally creates
unstaked booking codes through browser automation.

## Setup

Use Python 3.10+ in a virtual environment. The API path uses `requests` and
`python-dotenv`; browser booking additionally uses Playwright and Chromium.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install requests python-dotenv
# For browser booking:
python3 -m pip install playwright
python3 -m playwright install chromium
```

Settings load from the local `.env`, with defaults in `engine/settings.py` and
`infra/config/settings.py`. Keep an existing `.env`; for a fresh checkout create
one with your chosen `SEED_CODE`, collection bounds, and runtime settings.
`RESULTS_DIR` defaults to `results`, relative to the working directory.

## Commands

```sh
# Show options without contacting the provider:
python3 -m scripts.scan_code --help

# Decode the code configured as SEED_CODE in .env:
python3 -m scripts.scan_code

# Scan variants using the configured limits:
python3 -m scripts.scan_code --scan

# Browser booking of one fresh unstaked coupon:
python3 -m scripts.book_random --count 1
```

An explicit code can be passed to `scripts.scan_code`. SportyBet uses uppercase
base36 codes. `--env NAME` loads `.env.NAME` before the default `.env`.
`scripts.refresh_known` performs bounded refreshes of its fixed audit codes;
see its `--help` for required source and output arguments.

## Files and outputs

- `engine/`: coupon models, configuration helpers, scanning, logging, and archives.
- `infra/`: provider configuration, API client, and native response parser.
- `pages/`: browser page objects; `tests/data.py` holds selectors.
- `scripts/`: command entry points.
- `results/raw/`: archived API responses before filtering.
- `results/extracts/`: all qualifying decoded coupons for analysis.
- `results/codes/`: displayed code lists, subject to `TOP_N`.
- `results/booked/`: saved browser booking batches.

The shared [ticket-analysis project](../ticket-analysis/README.md) consumes
extracts from this provider and Bet9ja. Its offline suite includes SportyBet parser
and archive integrity checks. Run it from `Sport/ticket-analysis/`:

```sh
python3 -m unittest discover -s tests -v
```

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
python3 -m portfolio.reporting --provider sportybet --results ../sportybet/results
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
