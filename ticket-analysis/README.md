# Ticket analysis

The `portfolio` CLI compares existing Bet9ja and SportyBet extract files offline.
It requires Python 3.10+ and has no third-party dependencies. Run commands from `Sport/ticket-analysis/`
(`cd ticket-analysis` from the workspace root). The harvesters remain in the sibling
`../sportybet/` and `../bet9ja/` directories.

```sh
python3 -m portfolio --from ../sportybet/results/extracts ../bet9ja/results/extracts --output reports/first-run
```

If a provider's folder does not exist, omit it. Empty folders are allowed when another
input supplies extracts. Input folders are searched nonrecursively for `*.json`.

Named profiles have configurable odds and leg-count limits:

```sh
python3 -m portfolio --from ../sportybet/results/extracts --policy configs/profiles.example.json --output reports/profile-run
python3 -m portfolio --from ../sportybet/results/extracts --odds-basis parsed_leg_product --min-odds 10 --max-odds 200 --max-legs 12 --output reports/custom-run
python3 -m portfolio --from ../sportybet/results/extracts --codes sportybet:N5PZ7M --output reports/shortlist-run
```

Example profile boundaries are illustrations, not probability claims or recommendations.
Profiles can overlap; the combined report counts each provider/code once. CLI bounds
override all selected profiles; `--profile NAME` selects a profile (repeatable). Null
or zero disables a bound. Contradictory ranges are rejected. An explicit shortlist
still has to satisfy profile filters; exclusions win. Bet9ja code case is preserved;
SportyBet codes are uppercased.

Outputs in a **new** run directory:

- `similarity_report.txt`: per-code shared legs, counterpart codes and event warnings.
- `category_A_provisional.txt` / `category_B_provisional.txt`: combined candidate lists.
- `report.json`: all profile and combined results, exclusions, unclassified inputs,
  identical-content aliases, R1/R2, and optional R3 long-odds proxy (`--long-odds 3`).
- `manifest.json`: completion marker, effective policy, input and output hashes.
- `policy.json`: resolved policy, ready to pass to a replay's `--policy`.
- `inputs/`: exact source copies for replay; contains local data, not public fixtures.

`--top` limits only R2, never the candidate pool. R3 checks observed leg odds, not a
modeled probability. Different codes with identical content are listed as aliases:
both code count and distinct-content count are shown. Neither count is placed exposure.

All matching in this release is **provisional**, using provider + event ID + displayed
market + displayed pick. No normalization guesses or cross-provider matching are made.
Category A is not proof of independence or safety. Malformed/incomplete tickets and
conflicting observations of one code are quarantined instead of classified. For a
conflict, select the specific input snapshot to analyze; file modification times do
not establish historical observation order. Missing/unsupported ticket type does not
prevent descriptive comparison, but no ticket-loss or payout calculation is made.

These reports are about the supplied candidates, not current bookability, expected
value or provider-verified settlement. Actual stakes are included only when explicitly
supplied using `--positions`. Historical `available` flags are not live checks.
The CLI does not fetch codes, book tickets, or place bets. Changing filters cannot
recover candidates discarded by a previous harvest.

Each refresh is a new invocation with a new output directory. Existing directories
are refused. A run without `manifest.json` is incomplete and must not be used. Re-run
against a run's `inputs/` and effective policy to reproduce membership and comparison;
source paths and run timestamps will naturally differ. Preserve manifests and inputs.
Use `--previous reports/first-run/report.json` to show added/removed codes and A/B
transitions for the combined book and each profile. The prior report is copied into
the new run for provenance. Changes do not imply settlement or current bookability.

### Selected tickets and placed positions

Pass `--positions path/to/positions.json` to link candidates to previously placed
unsettled positions. Each position embeds its ticket snapshot; it is not silently
replaced by a freshly decoded code. Example shape (illustrative data only):

```json
{
  "positions": [{
    "position_id": "my-wager-001",
    "status": "placed",
    "stake": "100.00",
    "currency": "NGN",
    "profile": "longshot",
    "ticket": {
      "provider": "sportybet",
      "code": "EXAMPLE",
      "num_legs": 1,
      "selections": [{
        "event_id": "example-event",
        "event": "Example A - Example B",
        "market": "1X2",
        "pick": "Home",
        "odds": 2.0
      }]
    }
  }]
}
```

Use `selected` for proposed stake, `placed` for outstanding stake, and `settled`
with a nonnegative `payout` (gross return including returned stake) for user-reported
settlement. Repeated bets need distinct position IDs. This is a supplied ledger
snapshot, not an automatically verified or append-only provider ledger. Save each
revision separately; reports archive the supplied version. Only `placed` entries
contribute to outstanding selection/event exposure. Historical placed positions do
not have to match the current candidate profile filters to remain visible.

Amounts are calculated using decimal arithmetic and kept separate by currency.
Malformed position identities remain in monetary totals but are flagged as missing
from mapped exposure. Rows overlap: do not sum selection/event exposures as independent
losses. Settled net P&L is user-reported payouts minus settled stakes; no win probability,
drawdown timeline or provider-validated payout is claimed. Planned `selected` stakes
are excluded from actual-stake totals. Position profile labels are metadata; exposure
is currently combined across all supplied placed positions.

### Future harvest integrity

Both provider clients now archive response observations under their configured
`results_dir/raw/` before parsing/filtering (including unsuccessful lookups). This
adds disk use proportional to requests; the archives are local and ignored by Git.
Archival failure stops processing rather than silently losing evidence. No automatic
retention/deletion policy is enabled. Existing extracts are not rewritten.

New extracts include observation timestamps, raw archive references, native selection
keys and ticket structure. SportyBet rejects missing/ambiguous references and preserves
multiple selections on one event. It may now reject responses the old parser silently
accepted; inspect the raw observations before expanding support for a new schema.
`TOP_N` still controls the displayed code list, while JSON extracts now retain **all
qualifying tickets**. Collection odds limits still apply, and effective limits are
recorded. Nonqualifying responses remain available in the raw archive.

Run offline tests:

```sh
python3 -m unittest discover -s tests -v
```

The remaining roadmap is in `docs/RISK_ENGINE_IMPLEMENTATION_PLAN.md`. SQLite,
package consolidation, current-price/settlement collection, verified cross-provider
identity, an append-only position ledger and construction of new tickets are subsequent
increments.

### Directory relocation (2026-09-22)

The analysis package, tests, profiles, and reports now live together here.
`design/` became `ticket-analysis/docs/`; `gamble/` became `archive/gamble/`.
Historical reports and their input snapshots were moved without changing their
contents. Paths stored inside them refer to their original locations; resolve old
`Sport/reports/...` references under `Sport/ticket-analysis/reports/...`. Preserve
those recorded paths and hashes as historical provenance.

See [the workspace overview](../README.md) for provider commands and
[the documentation index](docs/README.md) for the design documents.

### Provider result presentation

Generate browser reports and per-scan plain-text summaries from saved provider
extracts, offline:

```sh
python3 -m portfolio.reporting --provider bet9ja --results ../bet9ja/results
python3 -m portfolio.reporting --provider sportybet --results ../sportybet/results
```

Each results folder gets an `index.html`, a `START_HERE.txt` listing scans, a `latest.txt`, and
`summaries/*.txt`. These are generated views; original evidence is unchanged.
Provider scanner saves automatically invoke this renderer. The browser shows
captured candidates and keeps site odds separate from calculated leg products;
it does not replace portfolio analysis or verify settlement.
