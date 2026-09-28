# Local run API (F6)

Run from `ticket-analysis/`:

```sh
python3 -m portfolio.serve --provider bet9ja --no-open
```

The existing report remains at `/`. `--provider` chooses that report; the API
supports both providers. `--results` changes only the report source. API runs use
`results/runs/<provider>/<run_id>/` under the repository workspace.
F7 will add the interactive page.

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
| POST | `/api/distinct` | `{ "run_id": "..." }`; pool metadata, exclusions, counts and distinct/overlapping tickets |
| POST | `/api/decorrelate` | `{ "run_id": "...", "max_exposure": 1, "target": 10 }`; full-pool metadata and selection/rejection explanations |

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
- `fresh_seed`: optional boolean; true returns 501 until booking orchestration
  is implemented. No implicit last-seed lookup or automatic booking occurs.

Mutation depth is fixed at 3. Client-supplied filesystem paths, environment
settings, odds basis and analysis band overrides are refused. Both analysis
routes consume the full eligible pool from the registry's exact hash-verified
byte snapshot, parsed once and using its recorded harvest policy. File replacement after lookup
cannot change the snapshot analyzed. Odds are labelled "recorded, not verified
payout". `max_exposure` defaults to 1 and must be >= 1; optional target must be
>= 1. Selection is greedy maximal, not guaranteed maximum. Fully distinct codes
are a subset of the cap-1 selection when no target limit is supplied; the counts
need not be equal.

Example (substitute a current seed):

```sh
curl -s http://127.0.0.1:8000/api/scan \
  -H 'Content-Type: application/json' \
  -d '{"provider":"sportybet","seed":"ABC123","try_budget":100,"want_count":5}'
curl -s http://127.0.0.1:8000/api/scan/RUN_ID
curl -s http://127.0.0.1:8000/api/distinct \
  -H 'Content-Type: application/json' -d '{"run_id":"RUN_ID"}'
```

Errors have `{ "error": { "code": "...", "message": "..." } }`. Status codes:
400 invalid input, 403 local/origin restriction, 404 unknown route/run, 409 busy
provider or unavailable/changed committed artifact, 413 oversized body, 415
wrong content type, 405 unsupported method on a known route, 422 unusable analysis extract, 500 server operation failure (including missing scanners),
501 fresh-seed booking unavailable. A 202 acknowledges the registry submission;
inspect `state`, since launch failure can already be recorded. Cancellation is
idempotent; poll until ownership has ended rather than assuming 200 means stopped.

Verification uses real local HTTP requests and fixture worker subprocesses for
both providers, with no provider traffic. Live provider acceptance and browser
acceptance remain separate gates; fresh-seed orchestration is still outstanding.
