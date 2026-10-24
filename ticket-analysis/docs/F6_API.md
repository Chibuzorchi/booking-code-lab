# Local run API (F6)

Run from `ticket-analysis/`:

```sh
python3 -m portfolio.serve --provider bet9ja --no-open
```

The existing report remains at `/`. `--provider` chooses that report; the API
supports both providers. `--results` changes only the report source. API runs use
`results/runs/<provider>/<run_id>/` under the repository workspace.
The three-stage console is at `/app` (F7): a static shell that calls only the
routes below, so every figure it shows comes from this API. It holds no run data
and can be served before any scan exists.

The server permits only loopback binding (`127.0.0.1`, `::1`, or `localhost`). All HTTP
requests (including report, version, and static files) also require a loopback peer and local Host with the server's port.
Browser requests must have the same Origin; cross-site requests are refused.
POST bodies must be JSON objects, at most 64 KiB. Unknown fields, duplicate JSON
keys, non-finite JSON constants, and duplicate query parameters are rejected.
There is no CORS opt-in. No endpoint places a paid bet.

| Method | Path | Request / result |
| --- | --- | --- |
| POST | `/api/scan` | Scan options below; 202 with registry status including `run_id` |
| GET | `/api/scan/<run_id>` | Registry status, nested `counters`, `stop_reason`, error and committed extract metadata |
| POST | `/api/scan/<run_id>/cancel` | `{}`; 200 with status (cancellation can still be pending) |
| GET | `/api/runs?provider=sportybet` | `{ "runs": [...] }`, newest first; provider optional |
| POST | `/api/codes` | `{ "run_id": "..." }`; pool metadata, exclusions and every eligible ticket in `codes`, with no analysis applied. For a run that has not succeeded it answers from the worker's `checkpoint.json` with `partial: true`, or 409 when nothing is saved |
| POST | `/api/distinct` | `{ "run_id": "..." }`; pool metadata, exclusions, counts and distinct/overlapping tickets |
| POST | `/api/decorrelate` | `{ "run_id": "...", "max_exposure": 1, "target": 10 }`; full-pool metadata and selection/rejection explanations |
| GET | `/app` | The three-stage console page (HTML); loopback-guarded like every other route |

Scan fields:

- `provider`: required, `bet9ja` or `sportybet`.
- `seed`: required explicit code; seven alphanumeric characters for Bet9ja
  (case preserved), six for SportyBet (uppercased).
- `want_count`: integer >= 0; default/blank/null 0 means uncapped output.
- `try_budget`: integer >= 1; default/blank/null 25000 bounds scan work.
- `min_odds`: finite non-negative number; default/blank/null/0 means no floor
  beyond the backend's minimum valid product of 1. Explicit values between 0 and 1
  are rejected.
- `max_odds`: finite non-negative number; default/blank/null 350000; explicit 0
  disables the ceiling. Positive maximum must be >= the effective minimum.
- `fresh_seed`: optional boolean, default false. True starts a run that books
  its own code first: the worker opens the provider in a browser, books one
  coupon, records the code in the run's `seed.json`, and then scans from it in
  the same process, under the same run id and the same provider lock. Supply
  `seed` or `fresh_seed`, never both (400). A provider whose tree has no
  `scripts/book_and_scan.py` returns 501. There is still no implicit last-seed
  lookup: nothing is booked unless the caller asks for it. Booking reserves a
  coupon code; it never stakes money.

Mutation depth is fixed at 3. Client-supplied filesystem paths, environment
settings, odds basis and analysis band overrides are refused. While it scans,
the worker rewrites `checkpoint.json` in its run directory (at most once a
second, atomically) with the qualifying coupons found so far, so a run that is
cancelled or fails keeps them. Only `/api/codes` reads it; `/api/distinct` and
`/api/decorrelate` still require a succeeded run. `/api/codes` and
both analysis routes consume the full eligible pool from the registry's exact hash-verified
byte snapshot, parsed once and using its recorded harvest policy. File replacement after lookup
cannot change the snapshot analyzed. Odds are labelled "recorded, not verified
payout". `max_exposure` defaults to 1 and must be >= 1; optional target must be
>= 1. Selection is greedy maximal, not guaranteed maximum. Fully distinct codes
are a subset of the cap-1 selection when no target limit is supplied; the counts
need not be equal.

For historical review, `/api/codes` additionally returns `saved_count` (the number
of retained extract rows), `expired_count`, and `expired_codes`. The latter contains
otherwise-eligible saved tickets hidden solely because a match has kicked off;
each carries `history_note` and the original `raw` ticket snapshot. These rows
remain in `excluded` and do not enter `codes`, `pool_size`, or either analysis.
The console's **Show expired codes** button reveals them with pagination and
copy support. Kickoff expiry is not a win/loss result; outcomes are not checked by
this endpoint. Other invalid rows remain excluded and are counted separately.

A fresh-seed run has no seed to report until booking finishes, so `seed` is
null while the run is active ("still booking") and `request.seed` stays null
for the life of the run. Once the code is booked, `seed` carries it on every
read, including in `/api/runs`. If booking fails, the run ends `failed` with
`error.code = "booking_failed"` and the provider's own reason in the message.

Example (substitute a current seed):

```sh
curl -s http://127.0.0.1:8000/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"provider":"sportybet","seed":"ABC123","try_budget":100,"want_count":5}'
curl -s http://127.0.0.1:8000/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"provider":"bet9ja","fresh_seed":true,"try_budget":100}'   # books its own code
curl -s http://127.0.0.1:8000/api/scan/RUN_ID
curl -s http://127.0.0.1:8000/api/distinct \
  -H 'Content-Type: application/json' -d '{"run_id":"RUN_ID"}'
```

Errors have `{ "error": { "code": "...", "message": "..." } }`. Status codes:
400 invalid input, 403 local/origin restriction, 404 unknown route/run, 409 busy
provider or unavailable/changed committed artifact, 413 oversized body, 415
wrong content type, 405 unsupported method on a known route, 422 unusable analysis extract, 500 server operation failure (including missing scanners),
501 the provider has no fresh-seed booking entrypoint. A 202 acknowledges the registry submission;
inspect `state`, since launch failure can already be recorded. Cancellation is
idempotent; poll until ownership has ended rather than assuming 200 means stopped.

Verification uses real local HTTP requests and fixture worker subprocesses for
both providers, with no provider traffic: the fresh-seed path is covered by a
fixture that books a fake code and hands over to the scan fixture exactly as
`book_and_scan.py` hands over to `scan_code.py`. Live provider acceptance and
browser acceptance remain separate gates: no test in this suite has opened a
real browser or booked a real coupon.
