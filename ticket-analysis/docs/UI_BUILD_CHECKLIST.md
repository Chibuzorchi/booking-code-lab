# Implementation Checklist — backend contract fixes before the UI

Fixes the contract issues from the 2nd review (UI_BUILD_PLAN.md §3, L10, L11) plus the
supporting flags the job runner needs. Each item: WHAT / WHERE / CHANGE / DONE-WHEN.
Order matters: F0 unblocks the runner; F1-F4 are the contract fixes; F5-F7 are the UI seam.

Legend: [BLOCKER] build cannot work without it · [HIGH] wrong results without it.

---

## F0. scan_code flags the runner needs  (small, do first)  [DONE 2026-09-27]
WHAT   The job runner drives scans by shelling out; it needs to set the output cap and
       write into a per-run folder from the CLI (today both are .env-only / fixed).
WHERE  bet9ja/scripts/scan_code.py, sportybet/scripts/scan_code.py (mirror both).
CHANGE - add `--max-qualifying INT` -> sets settings.max_qualifying (0 = uncapped).
       - add `--run-dir PATH` -> scan writes its extract (and codes) under PATH instead
         of results/extracts/, so a run is self-contained (plan L4).
DONE   `scan_code <seed> --scan --max-qualifying 0 --run-dir results/runs/<id>` writes
       exactly one extract under that dir; --max-qualifying 5 stops at 5 qualifying.
STATUS Flags wired and offline integration verified (external review: real main(),
       mutation gen, parser, scanner, archival + report subprocess with fixture HTTP;
       63 tests / 17 subtests pass). `--max-qualifying` -> settings.max_qualifying
       (scanner treats 0 as uncapped, bet9ja scanner.py:117 / sportybet:121); now
       rejects < 0 at the CLI. `--run-dir` -> settings.results_dir, so save() writes
       extracts/, codes/, index.html under the run folder; CLI now refuses a dir that
       already holds an extract (guards accidental re-run contamination of Stage 2's
       glob). NOTE: the re-run guard is a TOCTOU check, not a reservation -- two
       processes can both pass it before either writes (concurrent-start race). It stops
       accidental sequential re-runs, not concurrent starts. Atomic run ownership +
       exact committed-extract-path consumption (not dir globbing) stay OPEN under
       F5/F2 and are the real L4 fix. Negative-count + re-run guards verified to exit
       non-zero on both providers (bet9ja scan_code.py:63-68 / sportybet:68-73).

---

## F1. Stage-3 max_exposure domain  [BLOCKER]  [PARTIAL 2026-09-28 — validator done; API route/UI knob deferred to F6/F7]
WHAT   select_subset rejects max_exposure < 1; the plan/UI must never send 0.
WHERE  portfolio/selection.py:66 (raises ValueError). New API handler /api/decorrelate.
CHANGE - UI knob: integer, min = 1, default = 1 (= fully independent: no selection shared
         by two selected codes). Copy: "max codes any one selection may appear in".
       - API validates `max_exposure` is int >= 1 (and `target` int >= 1 if present)
         and returns HTTP 400 with a clear message BEFORE calling select_subset — never
         let the ValueError become a 500.
DONE   /api/decorrelate with max_exposure=0 -> 400 "must be >= 1"; with 1 -> a set whose
       codes share no selection.
STATUS Validator landed (branch fix/f1-exposure-validation). selection.py now exports
       ParamError + parse_decorrelate_params(raw) -> {max_exposure:int>=1, target:int>=1|None},
       defaulting max_exposure=1, and REJECTING 0/negatives/bools/non-integral/garbage
       BEFORE select_subset runs (so the future handler answers 400, never 500). Domain
       matches select_subset exactly -- test_parsed_output_is_accepted_by_select_subset
       proves parse output is never rejected downstream. Outer body is guarded too:
       None -> defaults; any non-mapping (falsy [] False 0 '' OR truthy [1] True 1 'x'
       3.5) -> ParamError "request body must be a JSON object", never AttributeError
       (so the 400-not-500 promise holds for malformed bodies). 11 validator tests;
       full suite 70 OK; direct `python tests/test_selection.py` now runs all 21 (the
       __main__ block was mid-file and skipped the new class -- moved to bottom).
       REMAINING (moves to F6/F7, not F1): wire /api/decorrelate to call parse_ first and
       map ParamError->400; add the UI integer knob (min 1, default 1) with the copy
       "max codes any one selection may appear in".

## F2. Stage-3 input shape + full pool  [HIGH] [PARTIAL 2026-09-28 — adapter tested; API wiring deferred to F6]
WHAT   select_subset needs tickets shaped {id, provider, num_legs, odds, legs:[{key,
       event, market, pick}]}. Raw extract coupons are NOT that shape (they have
       `selections` + `selection_key`, no `legs`/`key`). And it must run on the FULL
       harvested pool, not Stage-2's distinct list (the 42 are already disjoint).
WHERE  Adapter EXISTS: analysis.normalize_ticket(raw, provider) (analysis.py:133) emits
       legs[{"key",...}]. Reference pipeline: reporting.py:250-273 (read extract ->
       normalize_ticket -> drop issues -> dedup by id -> band -> select_subset).
CHANGE - /api/decorrelate uses portfolio.decorrelation.decorrelate_extract with one
         explicit committed run extract path, never a directory glob or distinct list.
         The runner resolves the path and provider; HTTP clients must not supply paths.
       - Normalize, exclude invalid/expired and foreign-provider tickets, deduplicate
         identical code observations; fail on conflicting observations without choosing
         a winner by filename or input order. Return exclusion reasons and pool counts.
       - Require explicit odds_basis + min_odds, optional max_odds=None (no ceiling).
         Missing odds on that basis are excluded without fallback. This exposes the
         policy choice; aligning it with harvesting remains F4's responsibility.
       - Keep every band-passing coupon, including overlapping ones.
       - Return result as a GREEDY MAXIMAL feasible subset (not "maximum"/"largest");
         surface result["rejected"] (reason + blocking selections) for explainability.
DONE   Deterministic three-code fixture: A/B share a selection, C is disjoint. Full
       pool size stays 3; cap 1 selects C and one of A/B; cap 2 selects all three.
       Fixed-clock tests exclude expired/malformed tickets, enforce each provider,
       prove explicit odds-basis filtering and isolation from neighboring extracts.
STATUS Offline adapter and nine tests implemented in portfolio/decorrelation.py and
       tests/test_decorrelation.py. Selection rejection details are preserved. Historical
       468 -> >=42 is not an acceptance oracle: expiry changes eligibility over time.
       REMAINING F6: wire route to the adapter using runner-owned extract/provider/policy,
       map ParamError to 400 and surface ExtractError as a failed analysis, not empty
       success. F5 still owns committed-path provenance; F2b owns shared stage identity.

## F2b. One leg-identity AND one eligible pool across Stage 2/3  [HIGH]  [DONE 2026-09-28 v2]
WHAT   Stage 2 distinctness and Stage 3 exposure are currently computed on DIFFERENT
       keys, so their notions of "same selection" disagree.
WHERE  Stage 2: uniqueness.selection_key (uniqueness.py:26) — prefers raw selection_key,
       NOT provider-namespaced. Stage 3: contracts.identity via normalize_ticket
       (contracts.py:19-30) — provider-namespaced, native + display fallback.
CHANGE Unify on contracts.identity as the single leg key: have uniqueness derive its leg
       key from normalize_ticket's legs[].key (drop its private selection_key), so
       both stages count shared selections on the SAME basis and eligible pool.
DONE   For one run BOTH stages build the SAME eligible pool from ONE builder
       (decorrelation.build_pool: same extract, provider, odds basis+band, duplicate/
       conflict handling) on the SAME leg identity. Corrected criterion: the fully-
       distinct set (Stage 2) == eligible codes whose every leg is unique across the
       WHOLE eligible pool. On the same pool with no target limit, distinct ⊆ selected
       at max_exposure=1; equality is possible. With a target limit, inclusion is not
       guaranteed.
STATUS Done (v2 — first pass FAILED review: Stage 2 had its own ingestion that ignored
       the odds band, accepted foreign-provider coupons, and diverged on conflicting
       duplicates). FIX: both stages now consume ONE builder, decorrelation.build_pool.
       uniqueness.load_coupons is gone; uniqueness.analyze(tickets) takes build_pool's
       normalized tickets and analyze_extract(extract, provider, *, odds_basis, min_odds,
       max_odds) is the Stage-2 entry. build_pool stamps ticket['raw'] so distinct_page
       keeps every display field. Leg identity = canonical(contracts.identity) on both
       sides. Integration tests (real extract files, not a _norm_legs helper) cover the
       three reproduced discrepancies: odds band applied in Stage 2; foreign provider
       excluded; conflicting duplicate raises ExtractError in BOTH stages -- plus the
       corrected subset criterion (distinct {C} is a strict subset of selected {C, A|B}).
       distinct_page/uniqueness CLIs now take --extract + --provider + odds policy (run-
       scoped), matching F0 --run-dir. Both CLIs REQUIRE --odds-basis. The distinct
       page displays, labels, and sorts by that basis without fallback. Regression
       coverage includes equal sets, target truncation, and odds display/CLI policy.

## F3. Provider isolation via worker processes  [BLOCKER]  [PARTIAL 2026-09-28 v4 — worker foundation implemented, verified offline (2nd + 3rd review findings fixed, F5 contract split); live dual-provider scan acceptance pending F5]
WHAT   Both engines mutate process-global state, so two providers cannot share one
       interpreter: env.py load_dotenv(override=False) means the 2nd provider inherits
       the 1st's env; each scan_code does sys.path.insert(0, provider_root).
WHERE  bet9ja/engine/env.py:14-22 + sportybet/engine/env.py:14-22; scan_code.py:13-18.
CHANGE - Job runner launches each scan/booking as a SUBPROCESS: cwd = Sport/<provider>,
          argv = [python, -m, scripts.scan_code, seed, --scan, ...flags]. Never import a
          provider's engine into the server process.
       - Single-flight: at most ONE active job per provider (a per-provider lock).
       - Separate run registries/dirs per provider; on provider switch in the UI,
         restore that provider's run state.
DONE   (LIVE acceptance, still pending F5 wiring): run bet9ja then sportybet back-to-back
       through the runner; the sportybet extract shows base36 codes and sportybet's own
       band (proves no env/path bleed). Not claimed yet — no provider traffic was used.
STATUS Implemented portfolio/worker.py (reviewed once; five findings fixed — see
       VERIFIED). WorkerHost.start_scan(provider, seed, *, max_qualifying=0, min_odds,
       max_odds, max_codes, depth, env_extra, run_dir) launches an ARGUMENT LIST
       (never a shell): [sys.executable, -m, scripts.scan_code, seed, --scan,
       --max-qualifying N, --run-dir <abs run dir>, ...] with cwd = <workspace>/<provider>
       and the server's own interpreter (worker.py:322-432; argv builder
       worker.py:135-153). Reuses the reviewed F0 flags with their verified semantics:
       --max-qualifying -> settings.max_qualifying (0 = uncapped, negatives rejected at
       the CLI) and --run-dir -> settings.results_dir (fresh-dir guard stays TOCTOU, see
       F0). Run dirs are ABSOLUTE: supplied relative paths are resolved against the
       host cwd before allocation/owner metadata/argv, so the extract can never land
       relative to the provider cwd; only a dir this call successfully created is ever
       removed by it — collision candidates and pre-existing runs are never touched
       (worker.py:327-351). ENV ISOLATION is explicit, not just cwd: isolated_env()
       strips a STATIC provider-config inventory (every key read by provider
       settings/env code, incl. LOCALE/TIMEZONE_ID/REQUEST_TIMEOUT_S absent from both
       dotenv files) PLUS every key any provider .env/.env.* defines PLUS
       PYTHONPATH/PYTHONHOME/BET_ENV, so the child's own load_dotenv(override=False)
       repopulates from ITS .env; env_extra wins (worker.py:47-133). The server process
       never imports engine/infra (asserted by tests). SINGLE-FLIGHT: per-provider flock
       on results/runs/<provider>/job.lock, opened by a tiny SUPERVISOR process and
       INHERITED BY THE WORKER, which retains the descriptor for its whole lifetime
       (worker.py:159-277): the lock is released only when every reference closes, i.e.
       when the worker exits — on either platform's flock semantics. Host death at ANY
       point (including mid-startup) cannot release the lock while the worker lives: a
       closed report pipe is treated as a host disconnect, and the supervisor keeps
       waiting on the worker instead of unwinding (worker.py:239-247). A supervisor
       killed while its worker lives leaves the lock held by that worker: replacement
       starts fail closed (JobBusyError) until it exits. NO recovery path ever signals a
       process identified only by a saved PID — there is no PID-based orphan reaping, so
       PID reuse cannot terminate unrelated processes. The supervisor writes
       job.owner.json {provider, run_id, pid, worker_pid, started_at}, reports
       ok/busy/error over a pipe (all report writes tolerate BrokenPipeError), and
       UNLINKS the sidecar before closing its descriptor — a stale owner can never
       delete a new owner's sidecar, and sidecar write failure terminates+reaps the
       worker (bounded) and surfaces as LaunchError. STARTUP FAILURE IS BOUNDED: on a
       missing report the parent reaps the supervisor with bounded SIGTERM->SIGKILL
       escalation (worker.py:449-477), then probes the flock itself (PID-free) to decide
       whether a live worker still owns the run dir — the dir is only deleted when no
       worker can be using it, otherwise LaunchError reports retained ownership
       (worker.py:433-439). SUPERVISOR EXIT vs WORKER COMPLETION are distinct in the
       F5-facing contract (worker.py:274-326): ScanJob.poll/wait/exit_code observe the
       SUPERVISOR (whose rc proxies the worker's on the normal path), while
       worker_alive() (advisory liveness of the live-reported worker pid — never
       signalled), worker_settled (worker confirmed gone), is_running()
       (supervisor OR worker alive), and WorkerHost.lock_held(provider) (authoritative,
       PID-free flock probe) observe WORKER completion. collect() waits for the
       supervisor and settles the worker only when it is already gone; the reaper keeps
       an orphaned-worker job registered (active_job stays non-None, lock_held stays
       True) until the worker exits. terminate() forwards through the supervisor and is
       a documented no-op once the supervisor is gone (escalation is F5 cancel
       semantics). A daemon reaper settles the job the moment the worker exits — success
       or failure — even if the caller never collects (worker.py:540-566); launch
       failure (Popen OSError) removes the owned run dir. Runs live under
       results/runs/<provider>/run-<provider>-.../ (extract via --run-dir, worker.log
       captures stdout/stderr). FRESH-SEED SEQUENCES DIFFER and stay unwired (inspected):
       bet9ja book_random books ONE seed (Chrome channel) and can --scan in-process but
       takes NO --run-dir/--max-qualifying; sportybet book_random books N seeds with NO
       scan step — booking launch construction is F5 work.
VERIFIED OFFLINE (tests/test_worker.py, 21 tests / 2 subtests; full suite from
       ticket-analysis/: `python3 -m pytest tests -q -p no:cacheprovider` -> 111 passed,
       21 subtests passed). Fixture providers mirror the real scan_code CLI +
       load_dotenv(override=False); every test crosses real subprocess boundaries, no
       provider traffic, Popen is not mocked; timing is synchronized via a started.marker
       worker-ready handshake (no fixed sleeps): (1) bet9ja->sportybet->bet9ja
       back-to-back with a tainted parent env incl. settings absent from dotenv — each
       extract shows its own provider/charset/band/URLs, no bleed in either direction;
       (2) concurrent starts: sequential AND simultaneous (thread barrier, and two
       processes launched together), same process AND across processes: exactly one
       accepted; (3) providers run independently; (4) launch failure, nonzero exit, and
       reaper path all settle ownership; HOLDER death (SIGKILL) keeps the lock held
       until the worker exits; (5) HOST DEATH BEFORE THE ACK: report-pipe closed ->
       supervisor stays alive, sidecar published, worker alive, lock held
       (JobBusyError); ownership ends only with the worker (sidecar removed, lock free,
       extract written); (6) SUPERVISOR DEATH BEFORE SIDECAR PUBLICATION (marker
       handshake, then kill): worker keeps the lock (no sidecar needed), replacement is
       refused until the worker exits — fail closed, no PID signalling; (7) CONTRACT
       SPLIT: supervisor killed while its worker lives -> supervisor exited but job
       still running, active_job non-None, lock_held True, worker_settled False; worker
       exit settles everything; (8) separate per-provider run dirs with correct
       attribution; (9) run-id collision exhaustion never deletes an existing run;
       (10) relative run_dir resolves before launch (log and extract co-located);
       (11) sidecar write failure releases the lock. Real provider entrypoints
       (`python -m scripts.scan_code --help`) launch offline under the isolated env and
       expose --max-qualifying/--run-dir.
KNOWN LIMITS: a worker orphaned by a SIGKILLed supervisor keeps the lock and blocks new
       starts until it exits (by design: fail closed; F5 provides explicit cancel/
       recovery). flock/supervisor design is POSIX (darwin/linux). Full durable status/
       cancellation semantics stay with F5.
REMAINING (tracked under F5/F6/F7, not F3): durable status.json transitions +
       cancellation semantics, API routes, UI provider-switch run-state restore, and the
       live dual-provider extract acceptance above.

## F4. Single odds basis end to end  [HIGH]  [DONE 2026-09-28 v2 — offline-verified both providers (review findings fixed); F6 API wiring + live confirmation remain under the whole-slice gate]
WHAT   "odds" means different things per stage: sportybet scanner qualifies on total_odds
       (site odds when present); reporting/uniqueness band on parsed_leg_product. Stage-1
       "5,000-350,000" and Stage-2/3 numbers can then refer to different quantities.
WHERE  sportybet/engine/coupon.py:108-112 (total_odds -> site when present);
       reporting.py:267 (in_band on parsed_leg_product); scanner qualify path.
CHANGE Pick parsed_leg_product as the canonical basis everywhere (provider-neutral,
       always computable). Make the scanner's min/max qualify compare parsed_leg_product
       (add ODDS_BASIS=parsed_leg_product setting if we want it switchable). Label the
       number in every stage/UI as "recorded odds (leg product), not verified payout".
DONE   A coupon whose site odds differ from its leg product gets the SAME in/out band
       decision at Stage 1 (scan qualify) and Stage 2/3 (report) — one basis, one answer.
STATUS Implemented, offline-verified for BOTH providers (no new env setting; no
       ODDS_BASIS switch — parsed_leg_product is the fixed basis, keeping worker env
       isolation untouched):
       - engine/scanner.py (both providers): qualification compares
         coupon.parsed_leg_product (all leg prices finite and > 1, product finite)
         against min/max (scanner.py:110-122); coupons without one are counted in
         ScanResult.unpriced and never qualify — never falling back to site odds.
         Site-displayed odds no longer gate the band. ScanResult records
         odds_basis="parsed_leg_product" + unpriced (scanner.py:41-42); save() exports
         both ("odds_basis", "unpriced_excluded", scanner.py:168-170); ranking, best()
         and the codes file all use the leg product (scanner.py:45,151,158).
       - engine/coupon.py (both providers): as_dict nulls out non-finite numbers
         (selection odds, site_displayed_odds, total_odds, computed_odds via _finite,
         coupon.py:28) so malformed/non-finite prices can never poison the extract
         JSON; parsed_leg_product stays None for them. Site odds remain a SEPARATE
         preserved field — never relabelled as leg product.
       - scripts/scan_code.py (both providers): the scan summary ranks and prints the
         leg product labelled "recorded leg-product odds ... NOT verified payout"
         (scan_code.py:88-92).
       - portfolio/decorrelation.py: harvest_odds_band(extract) translates the scan's
         recorded policy into the Stage-2/3 band (decorrelation.py:17-42): requires
         odds_basis == parsed_leg_product, maps the scanner's "no maximum" sentinel
         (max_total_odds <= 0, or not recorded) to max_odds=None, and REJECTS
         malformed/boolean/non-finite maxima (ExtractError) instead of silently going
         uncapped — an absent maximum is uncapped, an invalid one is an error.
         Contradictory positive bands are rejected too. build_pool/analyze_extract keep
         their explicit (odds_basis, min_odds, max_odds) contract unchanged;
         reporting's existing legends already label site vs calculated odds
         (reporting.py:68-69).
       - EXPORT HYGIENE: coupon.py as_dict nulls non-finite numbers at the top level
         (_finite, coupon.py:28) AND recursively through nested exported structures
         (_sanitize: odds_context, selected_systems, coupon.py:34-42), so a
         non-finite provider value (e.g. sportybet displayTotalOdds=Infinity stashed
         in odds_context by parser.py:137) can never corrupt the extract JSON; the
         extract is dumped with allow_nan=False as a backstop (scanner.py:190). The
         scan CLIs reject non-finite --min-odds/--max-odds-cap values outright.
       - BAND CONTRACT: inclusive on both ends at BOTH stages (scanner: odds >= min
         and (max <= 0 or odds <= max); build_pool excludes price < lower or
         price > upper). Missing/malformed/non-finite/overflow -> unpriced at Stage 1
         and missing_odds at Stage 2/3 — excluded by rule, never invented, no
         site-odds fallback. bet9ja side-effect: its old qualify used computed_odds
         (zero-lenient, rounded 2dp); it now uses the strict unrounded product, so
         sub-cent boundary cases can flip in/out — intended (one basis, no rounding).
VERIFIED OFFLINE (tests/test_odds_basis.py, 10 tests / 14 subtests; full suite from
       ticket-analysis/: `python3 -m pytest tests -q -p no:cacheprovider` -> 121 passed,
       35 subtests passed). Real scanner subprocesses per provider (offline fake
       clients over the real mutate/scan paths) — and the tests now save the ACTUAL
       scan() result through the real save(), translate its policy with
       harvest_odds_band(), and feed that extract into build_pool AND analyze_extract
       (capped and uncapped runs; tried/found_ok/unpriced/codes all asserted from the
       real result, nothing hand-built). Covered: site in-band/product out-band;
       product in-band/site out-band; exact lower and upper boundaries; above-cap;
       no-ceiling (max 0 == max_odds None, above-cap coupon joins the pool); missing,
       malformed (parser level), non-finite and overflowing prices (unpriced at
       Stage 1, missing_odds at Stage 2/3 — never invented, no site-odds fallback);
       harvest_odds_band rejects garbage/boolean/non-finite maxima and accepts
       absent/<=0 sentinels; a REAL parser->save->analysis regression for a coupon
       whose odds_context holds Infinity (strict JSON load succeeds, context value
       null, product still 6,400 and in-pool); both odds quantities survive with
       distinct values; report legend labels site vs calculated (not verified
       payout); codes file values equal the exported leg products.
REMAINING: F6 wires harvest_odds_band into the API using runner-owned extract+policy;
       the live end-to-end harvest->distinct->decorrelate confirmation sits under the
       whole-slice acceptance gate.

---

## F5. Run registry + job runner [PARTIAL 2026-09-28 v3 — review fixes completed; fresh-seed orchestration and live acceptance outstanding]
WHAT   Durable lifecycle and exact artifact lookup for F6/F7, implemented in
       portfolio/registry.py over portfolio/worker.py. No HTTP/UI implementation.
LAYOUT results/runs/<provider>/<run_id>/ contains status.json, status.lock,
       run.lock, diagnostic launch.pending, worker.exit.json, progress.json,
       worker.log, codes/, extracts/.
SCHEMA v1: run_id, provider, schema_version, state, request{seed, max_qualifying,
       min_odds, max_odds, max_codes, depth}, started_at, finished_at, updated_at,
       stop_reason, counters{tried, found_ok, qualifying, excluded_simulations,
       unpriced}, error{code,message}, cancel_requested_at, cancel_granted_at,
       supervisor{pid,exit_code}, worker{pid}, recovery_note,
       extract{path,sha256,size,validated_at,policy{odds_basis,min_odds,max_odds}}.
       Null counters mean unknown; found_ok is the scanner's valid counter.
       request records requested overrides; committed extract records effective
       scan settings and its translated odds policy. No environment secrets stored.
WRITERS Registry owns status.json (atomic replacement, status.lock serializes
       updates) and launch.pending. Worker owns progress/artifacts/logs; supervisor
       owns worker.exit.json. Progress is merged into live reads, never allowed to
       overwrite lifecycle state. Both scanners throttle atomic progress to 0.5s
       and atomically publish the completed extract. Stop reasons are
       qualifying_limit, try_budget, candidates_exhausted, cancelled, failure,
       or unknown. Qualifying limits count retained coupons, not requests.
STATES starting -> running -> succeeded|failed|cancelled|cancelled_unresolved|unknown;
       definitive launch failure -> launch_failed; cancelled_unresolved -> cancelled
       only after ownership ends. Other terminals are immutable.
START  Registry holds run.lock BEFORE publishing starting and passes that SAME
       descriptor into the supervisor, then into the worker. Host death before
       acknowledgement, missing/stale sidecars, and expired diagnostic timestamps
       cannot cause premature finalization. No PID/TTL-based launch-claim inference.
       Provider job.lock still enforces single-flight across processes. Losing a
       provider claim raises JobBusyError and removes only the new caller-owned run.
       LaunchError(uncertain=True) stays observable rather than terminal launch_failed.
       Invalid numeric arguments fail before allocating a run.
RECOVER Probe the RUN's inherited lock, not a provider-wide lock or saved PID.
       An unrelated run cannot keep an old record running. Once ownership ends,
       use worker.exit.json {provider,run_id,worker_pid,exit_code,finished_at}; reject
       evidence belonging to another run. A starting record may already have completed
       work if its host died before recording the acknowledgement. With no trustworthy
       completion evidence, report unknown; never infer success from file presence.
COMMIT Require successful completion, exactly one final extracts/*.json, valid strict
       JSON, matching provider, explicit parsed_leg_product basis, and a valid band.
       Validate and hash ONE byte snapshot; record its digest, size, and exact path.
       An empty valid extract is successful. committed_extract() uses only the saved
       path and checks containment, existence, digest, and size on every retrieval;
       replacement/truncation/mutation fails closed. The hash check applies at lookup;
       callers must not treat arbitrary later filesystem mutation as an immutable file.
CANCEL Persist request first. The owning finalizer consumes requests from ANY registry;
       repeated requests do not spawn repeated escalation threads. Signal the spawned
       supervisor, which terminates its own child; after grace, SIGUSR1 asks that same
       supervisor to kill its direct child. No ps lookup or PID-based kill in registry.
       If the supervisor/control channel is gone, remain cancelled_unresolved until
       run ownership ends. Completion updates counters and clears unresolved errors.
       Terminal cancellation is a no-op; cancellation during launch prevents startup
       if observed before Popen, otherwise the owner consumes it after acknowledgement.
VERIFICATION See tests/test_registry.py: original lifecycle coverage plus all five review
       regressions, integrity mutations, malformed parameters, run-bound exit evidence,
       and REAL host crashes before launch and after acknowledgement. The latter writes
       a valid extract then exits 7; recovery must fail it. A deliberately old diagnostic
       claim cannot override an active run.lock. Registry tests use fixture providers and
       real subprocesses; full-suite results are recorded in the implementation handoff.
LIMITS Fresh-seed booking orchestration is NOT implemented. The differing book_random
       paths require a separately tested book -> seed -> scan flow under one provider
       job. Scan-only cancellation targets the worker via its supervisor; full browser/
       descendant cleanup for booking remains unimplemented. POSIX flock only. Atomic
       replacement prevents torn reads but does not claim power-loss durability (fsync).
       Saved PID liveness remains advisory in WorkerHost; inherited locks are authoritative.
       F6 must wire routes/loopback, use harvest_odds_band and committed_extract; F7 owns
       UI restore/polling. LIVE HTTP/full-provider acceptance remains outstanding.
DONE   After F6: GET /api/scan/<id> shows live counts and stop_reason; cancellation ends
       the job. Fresh-seed orchestration and live whole-slice acceptance remain gates;
       do not mark the entire F5 item DONE from these offline tests alone.

## F6. API routes on serve.py [IMPLEMENTED — offline HTTP verification; live acceptance pending]
WHAT   Add POST handlers; serve.py is GET-only today (serve.py:67).
CHANGE POST /api/scan, GET /api/scan/<id>, POST /api/distinct, POST /api/decorrelate,
       GET /api/runs. Enforce loopback (or auth) since these trigger scans/bookings —
       do NOT rely on the 127.0.0.1 default alone (--host is configurable, serve.py:103).
DONE   Each route works; a non-loopback request to a mutating route is refused.
STATUS Added all five routes plus POST /api/scan/<id>/cancel. Loopback binding,
       peer/Host/Origin checks on every report/static/API request, bounded strict JSON
       requests, normalized origin authorities, and structured API errors (including
       unexpected failures and unsupported methods).
       Stage 2/3 parse the exact verified snapshot once and use harvest_odds_band;
       Stage 3 validates parameters before registry access and keeps the full pool.
       Existing report remains available. See F6_API.md for the HTTP contract.
       Explicit seed required; fresh_seed=true returns 501. Booking orchestration,
       live provider acceptance, and F7 browser work remain outstanding.

## F7. Shared copy() helper + 3-stage page
WHAT   Copy JS is duplicated (reporting.html, distinct_page.py) and distinct_page's
       per-code copy has no fallback.
CHANGE Extract one copy helper (clipboard + select-text fallback) used by both the
       distinct view and the 3-stage page. Build the page: Stage1 form -> poll ->
       Stage2 distinct (per-code Copy + Copy-all) -> Stage3 decorrelate.
DONE   Per-code copy works with the fallback path; one helper, no duplication.

---

## Acceptance gate for the whole slice
1. bet9ja + sportybet each: scan (band, uncapped+budget) -> distinct -> decorrelate,
   all from the browser, each provider in its own process.
2. With no target limit, Stage 2 fully-distinct codes are a subset of Stage 3
   cap-1 selected codes on the same eligible pool (F2b); counts need not be equal.
3. max_exposure=0 is impossible to send; the pool fed to Stage 3 is the full run pool.
4. Every odds figure is one basis, labelled "recorded, not verified payout".
