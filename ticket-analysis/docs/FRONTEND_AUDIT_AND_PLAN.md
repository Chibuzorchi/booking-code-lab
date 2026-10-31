> Recovered on 2026-10-31 from the original October 25 session record.
> Original location: `/private/tmp/booking-ui-audit/AUDIT_AND_PLAN.md` (temporary file no longer present).
> The original text below is preserved unchanged; its status statements describe the audit date, not current progress.

## Current progress — 2026-10-31

Stages 1–5 are merged into main (stage 5 in PR #12). See [Stage 5 changes and validation](FRONTEND_STAGE_5_REVIEW.md). Stages 6–10 remain pending. Stage 9's original backend-availability assumptions require reassessment against the newer results backend.

---

# Frontend audit and incremental redesign proposal

Audit date: 25 October 2026. Repository: `Chibuzorchi/booking-code-lab`, local HEAD `0e63f02`.
Status: **audit only; implementation requires explicit approval.** No application source, tests, API contracts, or saved extracts were edited. Working tree was clean at completion.

## Recommendation

Retain the Python server and vanilla JavaScript. Replace the long three-stage page with a persistent application shell and focused workflow views. Make selected-run context a first-class part of every dependent screen. Fix misleading visibility and accessibility defects before moving controls. Separate source extraction, navigation changes, and workflow redesign into individually approved stages.

Do not introduce a frontend framework or build pipeline: the application has one interactive console, a useful state coordinator, and a substantial existing test suite. The immediate complexity is information architecture and tightly coupled DOM rendering, not a missing framework.

**Material scope mismatch:** this checkout does not implement the Historical Results screen described in the request. It has expired-ticket viewing and F9 design documents. `portfolio/api.py` has no historical-evaluation endpoint; `app.html` has no outcome checker. `docs/F9_RESULTS_PLAN.md` and `F9_PLAN_QA.md` explicitly say the results backend and source/rule coverage are not implemented. A working result checker cannot be delivered through styling or client-side calculations. Its future integration must have a separate backend-readiness gate.

## Actual architecture and constraints

| File / area | Responsibility and redesign implication |
| --- | --- |
| `ticket-analysis/portfolio/app.html` (1,071 lines) | HTML layout, all console CSS, request coordination, provider state, polling, list preferences, pagination, and result rendering. Most redesign work belongs here initially. |
| `portfolio/pages.py` | Produces `/app` HTML and inlines the shared clipboard helper. A safe seam for extracting CSS/JS while retaining current serving behavior. |
| `portfolio/copy.js` | Shared by console and standalone reports. Clipboard, legacy copy, and selectable-text fallback must all survive. |
| `portfolio/serve.py` | Python HTTP server; `/app`, legacy report at `/`, loopback/Host/Origin protections, API error handling. Its normal startup archives stale provider extracts; an audit should avoid unnecessary restarts. |
| `portfolio/api.py` | `/api/scan`, status/cancel, `/api/runs`, `/api/codes`, `/api/distinct`, `/api/decorrelate`. Strict request fields and errors. No historical-result endpoint. |
| `portfolio/registry.py`, `worker.py` | Run ownership, per-provider concurrency, progress, committed extract integrity, partial checkpoints, cancellation and recovery. Preserve intact. |
| `portfolio/decorrelation.py`, `uniqueness.py`, `selection.py`, `analysis.py`, `contracts.py` | Eligibility, selection identity, odds policy, distinctness, exposure constraints. These are business logic, outside the frontend redesign. |
| `portfolio/reporting.html`, `reporting.py`, `distinct_page.py` | Separate report surfaces; keep working and smoke-test when changing shared asset assembly/copy code. |
| `bet9ja/`, `sportybet/` | Separate provider processes and dependencies; fresh-seed booking uses provider browser entrypoints. No provider-package consolidation needed. |
| `tests/test_pages.py`, `fixtures/console_probe.js`, `copy_probe.js` | 34 page/helper test methods, including executable JavaScript with a DOM stub, races and provider restoration. The stub does not exercise real CSS/layout or assistive technology. |
| Remaining `ticket-analysis/tests/` | API, registry, worker, integrity, selection, normalization and reporting tests. Entire suite: 227 passing. |

There is no frontend package manifest, framework, or checked-in Playwright suite for this console. Python Playwright and Chromium are available locally. Existing provider automation does not constitute console end-to-end coverage.

The console holds separate in-memory views per provider, rejects stale asynchronous responses with generation/request ownership checks, polls every three seconds with one status request in flight, and paginates saved/distinct results at 25 rows. De-correlate currently renders its entire selected/rejected lists without pagination. A page reload discards the selected provider/run and analyses.

## Workflow contract to preserve

```text
Typed seed OR fresh-seed booking
           |
           v
      Provider scan -----------------> checkpoints -> saved-code viewing
           |                             (partial; can survive failure/cancel)
           v
Succeeded run + verified committed extract
           |
           +--> eligible pool --> Distinct analysis --> distinct display/filter/copy
           |
           +--> eligible pool --> De-correlate --> selected set + exclusion reasons

Existing UI gate: De-correlate unlocks after Distinct returns for the selected run.
Its DATA INPUT remains the full eligible pool, not the distinct result.
```

- A cancelled/failed run can expose retained checkpoint tickets; it cannot feed either analysis.
- `cancelled_unresolved` still represents provider ownership: keep polling and block another launch for that provider until resolved.
- `try_budget` can be a successful completion reason. Display “Completed — attempt limit reached,” not failure or unfinished saving.
- Bet9ja code case is preserved; SportyBet uppercases six-character codes. Defaults and blank/zero meanings stay unchanged.
- Both analyses use the committed extract and recorded odds band; list filters/sort/pagination are display-only. They never change analytical inputs or qualify a different historical population.
- Max exposure is at least one. With cap one and no target limit, fully distinct codes belong to the selected set when evaluated over the same eligible pool. A target limit removes that guarantee.
- Revealing expired tickets changes viewing/copying, not eligibility or analysis. “A match has started” does not imply the ticket lost, finished, or has a checked outcome.
- Copying a filtered list currently copies all matching rows across pages, not merely the 25 rendered rows. Preserve behavior and make the label explicit.
- Registry status counters, persisted checkpoint count, eligible count, and historical population are different quantities. Never substitute one for another when a value is unavailable.
- Eligibility is time-sensitive; committed bytes are immutable, eligibility is not. The current successful-run list is cached while subsequent analysis requests reevaluate eligibility. Surface request/check time and differences without freezing or changing backend rules.
- Future F9 history must evaluate retained snapshots, including expired/overlapping tickets, independently of current eligible/distinct/selected lists. Partial history requires an explicitly frozen input and incomplete-population label under the F9 contract.

## Evidence and prioritized findings

| Priority | Finding | Evidence / consequence | Proposed disposition |
| --- | --- | --- | --- |
| High | Hidden controls are visibly rendered and focusable | `.bar {display:flex}` overrides `hidden` on `s2-bar`, `s2-pager`, `s3-bar`. Empty startup shows Copy/Prev/Next without results. DOM-stub tests pass despite the CSS defect. | Enforce hidden semantics and add a real-browser regression test first. |
| High | Shared ticket layout clips actions | Five-column summary grid receives six children for historical rows and De-correlate rows. In a measured expired row, Copy occupies a 20px track (30px element), is clipped, and the chevron wraps to another row. | Correct shared row layout before workflow relocation; later replace repeated card labels with a compact consistent list. |
| High | Run selection is buried and unreadable | History sits below the saved-code list; 39 registered runs (19 Bet9ja, 20 SportyBet) are long identifier/status/seed options without date columns, search, or counts. | Dedicated Saved Runs table, human dates and provider/seed/status; secondary expandable technical ID. |
| High | Saved vs eligible counts are easy to misinterpret | Real 24 October Bet9ja run: 334 saved, six eligible, 328 expired at audit time. Counts are explained in a long paragraph under generic “All codes.” | Separate saved/upcoming/started counts, explicit eligibility labels and check time. Preserve original snapshots and exclusions. |
| High | Excessive page length makes workflows hard to reach | Before history is loaded the 1440px page is 1,384px tall. With historical rows and both analyses open: 7,881px at tablet width, 12,445px at 390px, 12,793px at 320px. | Focused views with persistent navigation; keep long tables within their workflow. |
| High | Locked-state explanatory text has inadequate contrast | Whole-panel opacity `.62` blends normal helper text to approximately 2.62:1 against its surface; these explanations are essential for unlocking the workflow. | Dim disabled controls only. Retain normal readable labels/explanations. |
| High | Historical evaluation is unavailable, not merely cluttered | No outcome API or result view in the current code; F9 is a reviewed plan. | Honest unavailable state with access to saved ticket history; gate evaluation UI on a real contract/backend. |
| Medium | No persistent navigation or selected-run context | Provider sits inside the new-scan form; the selected run is a raw status sentence. Reload returns to Bet9ja / “No run yet.” | Global provider/run context; URL view navigation; verified run restoration using read-only requests. |
| Medium | Status presentation exposes implementation details | `succeeded`, `try_budget`, `cancelled_unresolved`, raw provider browser stack text, large IDs compete with the next action. | Human-facing state summaries with technical detail disclosure. Preserve exact diagnostic content in that disclosure. |
| Medium | Error and analysis updates lack announcement semantics | Error paragraphs have no alert/live role or field associations. Only scan status is live. Analysis buttons disable without a clear pending label/region. | Relevant `aria-busy`, status/alert messages, inline validation and explicit retry where appropriate. |
| Medium | Empty or unselected history allows a no-op action | “Use this run” is enabled after history load even without a selected run; handler silently returns. | Enable only for a selectable run; explain empty/filtered/no-match states. |
| Medium | One page mixes scan configuration, running job and previous results | Wrapped controls give inconsistent hierarchy; “Book a fresh code” competes with an enabled-looking typed-seed field until checked. | Explicit seed-source choice; one primary launch action; separate status and saved-code regions. |
| Medium | Partial results can conceal data unavailability | `loadCodes()` suppresses errors for non-succeeded runs. Its request stamp can prevent same-count retries after an error or checkpoint update. Poll errors stop polling. | Separately approved frontend feedback/retry changes; no claims that missing data means zero or that cancellation saved every hit. |
| Medium | Terms overstate analytical certainty | “fully independent — no shared games” and cap-one validation wording exceed what shared selection identities prove. | “No shared selections within this set”; explain scope without changing tier calculation/API values. |
| Medium | De-correlate detail is not the full ticket | Current expanded row shows shared selections only, sometimes just a no-overlap message. `selection.selected` lacks complete legs. | Label shared-selection detail accurately; offer full ticket inspection using existing saved-code snapshots only when available and attributable to the selected run. |
| Medium | Long histories lack search and reusable detail context | Only odds sort/filter and pagination exist; repeated labels increase row height. | Local search/filter over fetched results, contextual detail, predictable reset and copy labels. |
| Low | Control boundaries and focus need consistent treatment | Form-border contrast is about 1.74:1 against white; fallback textarea is absent from the custom focus selector. Buttons generally already meet a 44px height. | Stronger boundaries and consistent visible focus; retain native controls and generous targets. |

**Strengths to retain:** native labelled controls; real disabled fieldsets; visible focus outlines on most controls; semantic `details` and table headers; lazy detail creation; stable odds sorting; copy fallback; honest unchecked-outcome labels; generation guards; per-provider restored analyses; strict API validation; no external UI dependencies.

## Proposed information architecture

A collapsible left sidebar on desktop, compact accessible navigation on narrow screens. Main content has one heading, a compact selected-run context strip, a clear primary action, and relevant status. Sidebar destinations:

1. **Scan Codes** — provider, seed source, odds limits, desired count; advanced attempt limit; status and saved codes for the selected run.
2. **Saved Runs** — searchable run table, status/provider filters, selected-row indicator, View codes / Use for analysis actions. No delete or rename feature is added.
3. **Distinct Codes** — selected run, eligibility basis, Run analysis, compact summary, searchable/sortable distinct list. Unavailable state links to Saved Runs or Scan Codes.
4. **De-correlate** — same run/pool, understandable selection cap and target, Run selection, selected summary/list, progressively disclosed rejection reasons.
5. **Historical Results** — initially an honest unavailable screen explaining that saved selections can be inspected but outcomes are unchecked. When the real backend exists: summary -> filtered code list -> selection/evidence detail, with unresolved/unsupported/incomplete categories preserved.

Start on Scan Codes. A separate Dashboard would duplicate run totals without adding a current task, so it is not proposed initially. Saved-code inspection belongs to the selected run, available from Scan Codes and Saved Runs, rather than another competing sidebar destination.

Use hash-based client navigation under `/app`, preserving the current server route/security. Section changes do not invalidate a run or throw away analysis. Provider/run changes still use the existing invalidation rules. Browser back/forward and refresh restore a validated provider/run identifier, never automatically submit a scan or claim cached analysis is current. Maintain heading focus and per-view scroll position; hidden views must leave keyboard and accessibility navigation.

Retain native selects for provider, ordering and short status filters. Replace only the long run selector with a dedicated table/search workflow. A “Change run” action opens Saved Runs while remembering the caller so selection can return to that workflow. No custom combobox is needed.

## Visual and interaction direction

- System typography; 14–16px primary text, readable secondary text; tabular numbers for counts/odds.
- Shared spacing tokens (4/8/12/16/24/32px), neutral surfaces, restrained existing green accent, subtle borders and minimal shadow.
- One prominent action per view; secondary actions remain bordered/text buttons; cancellation is distinct and status-specific.
- Compact summaries distinguish Saved, Eligible now, Started, and Other exclusions where the API supplies them. Missing values use an explicit unavailable state, not zero.
- Short explanations next to choices; advanced configuration, technical run identifiers, provenance and raw diagnostics behind labelled disclosure.
- Tables at desktop widths; labelled compact rows on mobile. Selection detail tables may scroll horizontally within their own region. Avoid page-wide overflow.
- Use normal text contrast for lock explanations, explicit state labels rather than color alone, native controls, contextual copy button names, and tested focus behavior.
- Honest progress: tried count / attempt budget where meaningful, separate booking and scanning phases; no invented percentage/ETA or guaranteed output count.

This applies [Apple's sidebar guidance](https://developer.apple.com/design/human-interface-guidelines/sidebars) to top-level navigation and limited space; [Nielsen's heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) to system status, recognition, user control and error recovery; and [progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) to advanced controls and evidence. Accessibility acceptance uses [WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/), particularly [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html), and [focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html). This audit is not an accessibility conformance certification.

## Approval sequence and stage boundaries

Every numbered stage is separate. At its start, restate files, intended changes, preserved behavior and acceptance checks. After validation, report changes/pass/fail/concerns and stop for explicit review. Prefer one Git commit per accepted, validated stage. Any failing test is investigated before proceeding; no assertion is removed or weakened to conceal a failure. A changed visible label may require updating its explicit text expectation while preserving the behavior it protects.

### 1. Shared control correctness and accessibility foundation

**Files:** `portfolio/app.html`; new `tests/test_browser_ui.py` with Python Playwright; existing `tests/test_pages.py` only if extending meaningful coverage.

**Change:** enforce hidden visibility; correct ticket summary columns for normal/history/selected rows; retain readable locked-state explanatory text and control boundaries; make error feedback programmatically discoverable. Do not change navigation, algorithm inputs, or run transitions.

**Preserve/check:** initially absent controls are neither visible nor tabbable; normal/history/De-correlate row Copy and expansion work; keyboard/focus and error messages; 320/390/768/1440px. Existing page/copy tests plus the full baseline suite. New browser tests must catch the CSS defects the DOM stub misses.

**Approval deliverable:** small shared-component diff and before/after screenshots. Stop.

### 2. Behavior-preserving frontend asset extraction

**Files:** `portfolio/app.html`, new `portfolio/app.css`, new `portfolio/app.js`, `portfolio/pages.py`, `tests/fixtures/console_probe.js`, page/serve tests.

**Change:** move CSS and JavaScript into dedicated source assets; inline them through the existing page assembly seam. Keep the state coordinator/handlers intact. Keep shared clipboard behavior centralized. No new framework, bundler, API, or asset-serving route.

**Preserve/check:** same served behavior, one copy helper, script parsing, unchanged asynchronous race assertions. Update the probe's source loader to execute the real extracted source; preserve its assertions. Smoke-test console, legacy report and standalone distinct page. Stop.

### 3. Application shell, navigation and selected-run context

**Files:** `app.html`, `app.css`, `app.js`, console probe/browser tests.

**Change:** collapsible sidebar; separate existing Scan/Distinct/De-correlate panels; move existing history controls to Saved Runs without yet replacing the selector; global provider/run context; hash navigation and validated reload/back/forward restoration. Move existing DOM deliberately without rewriting workflow rendering.

**Preserve/check:** provider isolation, request ownership, active run polling/cancel behavior, analysis gating, filters and pagination on section changes. Restore identifiers through GET requests only; do not auto-run analyses/scans. Keyboard navigation, active location semantics, focus/scroll, narrow layout, no hidden-view tab stops. Stop.

### 4. Saved Runs selection experience

**Files:** `app.html`, `app.css`, `app.js`, console probe/browser tests.

**Change:** replace long run selector with searchable, filterable table using `/api/runs`. Date/time with timezone, provider, seed, human status, count where supported, chosen-row indicator. Technical ID/details remain accessible. Return to the requesting workflow after selection.

**Preserve/check:** succeeded/failed/cancelled and no-run states, mismatch rejection, stale history replies, run-change analysis clearing. Completed count comes from committed status; partial “found” counters must not be advertised as durable saved counts. Missing counts stay unknown unless a saved-code response supplies them. No per-row analysis requests just to fill the table. Stop.

### 5. Scan configuration and job status

**Files:** `app.html`, `app.css`, `app.js`, browser/console tests.

**Change:** seed-source choice, clearly grouped ordinary inputs and advanced attempt budget, single launch action, booking/running/cancelling/completed/error states, compact meaningful counters, readable stop reasons, diagnostics disclosure. Scope explicit retry/status-refresh UI here; do not change worker behavior.

**Preserve/check:** exact request payloads/defaults; seed case; fresh/typed mutual exclusion; per-provider single flight; pending cancellation ownership; submission while switching provider; errors/partial results; retry cannot duplicate a job. Intercept browser launch requests in deterministic tests; run existing worker/API fixture tests for lifecycle coverage. Stop.

### 6. Saved-code list and ticket inspection

**Files:** `app.html`, `app.css`, `app.js`, browser/console/copy tests (change `copy.js` only if a shared helper defect requires it).

**Change:** compact saved-code list, explicit eligible/started/exclusion summary, code search and odds toolbar, accessible selection details and clear copy-all-matching label. Existing default eligibility view and explicit historical reveal remain intact. Provide honest no-data/unavailable/partial states and a deliberate retry for list failures without altering population rules.

**Preserve/check:** expired toggle never feeds analysis; original selections remain unchanged; filters stay per provider/run and separate from Distinct; pagination retains every matching code; copy spans all filtered pages; denied-clipboard fallback; unavailable checkpoint is not zero saved. Recheck duplicate/exclusion accounting and keyboard focus after rerenders. Stop.

### 7. Distinct Codes view

**Files:** `app.html`, `app.css`, `app.js`, browser/console tests.

**Change:** concise selected-run and eligible-pool explanation, primary analysis action with pending/error states, compact input/eligible/distinct/overlap summary, local code search plus existing odds filters, reusable list/detail. Put top reused selections under clear disclosure.

**Preserve/check:** entire eligible pool remains the analysis input; search/sort/band changes do not make requests or change De-correlate input; empty pool vs no filter matches; successful result unlocks only this run's De-correlate; stale replies remain ignored. Check cap-one subset invariant where applicable. Stop.

### 8. De-correlate view

**Files:** `app.html`, `app.css`, `app.js`, browser/console tests.

**Change:** explain “Maximum codes sharing one selection” and optional target, show actual selected count/cap, compact paginated selected and rejection lists, precise selection-sharing language and diagnostics. Preserve current Distinct-first UI gate. Full ticket inspection uses the matching run's existing saved snapshot when available; shared-selection details remain clearly labelled.

**Preserve/check:** full pool, not filtered/distinct list; minimum cap one; target and no-target behavior; subset invariant only when applicable; realized exposure, rejection reasons and odds basis; all-selected copy despite pagination; repeated submissions and provider switches. No claim of statistical independence or financial risk estimation. Stop.

### 9. Historical Results availability and future integration gate

**Files now:** `app.html`, `app.css`, `app.js`, browser tests; a concise integration checklist document if needed.

**Change now:** a truthful Historical Results destination explaining unavailability and linking to retained ticket inspection. No enabled “Check results” action, synthetic totals, invented outcome records, or browser-side settlement inference.

**Preserve/check:** expired/unchecked is distinct from unresolved evaluation and loss; no fabricated outcome API request; saved-code and analysis flows still work.

**Conditional future stage, requiring separate scope approval:** once F9 supplies a verified API, add outcome/coverage summary, search/status filters, details/evidence panel and source-limit disclosure. Before implementing, agree the endpoint/schema, immutable population identity, complete/partial accounting, outcome categories/denominators, job lifecycle and real acceptance evidence. Backend construction is not silently folded into this frontend stage. Stop.

### 10. Final cross-workflow acceptance review

**Files:** targeted UI/test fixes only if this review exposes issues; record acceptance evidence.

Run the full test suite and browser journeys against the agreed final scope: new and historical runs, both providers, cancelled/failed runs, filter/copy/selection behavior, request races, context/back/forward/reload, responsive layouts and keyboard access. Capture matched before/after screenshots. Validate shared standalone reports. No broad architectural cleanup in this stage. Report any backend-gated functionality as unavailable, not complete.

## Validation performed during this audit

- Repository architecture, source handlers, API dispatch, registry transitions, pool/selection contracts and prior investigation/F9 documents inspected. Older UI build reviews contain stale facts; current source was treated as authoritative.
- The URL initially refused connection. The ordinary application server subsequently became available on 8000. Connected browser access returned `cross_origin` 403; a local Chromium Playwright process accessed the real app successfully without weakening server guards.
- Real run inventory: 39 records: 32 succeeded, four cancelled, three failed. Both provider histories inspected.
- Actual backend/read-only browser journeys: completed run selection, eligible and expired lists, ticket detail, distinct analysis, view filtering/reset, De-correlate, provider switch/restore, failed booking, cancelled run checkpoint history, reload, pagination and copying. Analysis POSTs compute reports and do not launch provider jobs.
- Sample real counts: Bet9ja 24 October run 334 retained / six eligible / 328 expired; SportyBet 23 October run 62 retained / three eligible / 59 expired. These are audit-time observations, not permanent expected counts.
- Keyboard Enter expanded a real ticket containing 28 selections. Page 2 of the 334-code list rendered 25 rows; Copy all-matching captured 334 codes. Clipboard capture used a test-only browser shim; existing helper tests cover success/denial/fallback paths.
- Controlled browser route fixtures (not production data): submission pending, fresh-seed starting, running, cancellation pending, cancellation complete, history loading, service error, empty history. Real provider scans/booking were not launched. Provider network availability and new fresh-code booking success were not verified live.
- Existing executable console tests cover stale replies, single-flight polling, provider-specific submission/error restoration, filters and copy behavior. All 227 unit/integration tests pass. Initial sandbox run had 23 local-socket permission errors; unrestricted rerun passed all tests in 71.077 seconds.
- Captured 1440px desktop, 768px tablet, 390px and 320px mobile screenshots. No document-wide horizontal overflow in those captured states; there is local clipping in ticket rows. Mobile page length is the dominant navigation problem.
- No JavaScript page errors in the primary real-data browser run. Error-region/hidden-state DOM checks and calculated contrast measurements performed. No full screen-reader session or complete WCAG certification.
- Live `launch_failed`, `unknown`, new successful fresh booking, and all possible server failures were not produced during the audit. Source and existing fixture tests cover lifecycle boundaries; future stages should expand deterministic browser coverage where affected.

## Evidence files

- `browser-results.json`: real-data workflow and viewport observations. Its first immediate ticket-row count sampled before the lazy toggle handler completed; the later awaited interaction audit correctly verifies 28 rows.
- `interaction-results.json`: keyboard, hidden controls, clipped layout geometry, copy counts, failed/cancelled runs, reload and controlled transient states.
- `browser_audit.py`, `interaction_audit.py`: temporary Playwright audit scripts.
- `before-desktop-empty.png`, `before-desktop-completed.png`, `before-desktop-expired.png`, `before-desktop-all-workflows.png`.
- `before-expired-row-viewport.png`, `before-error-viewport.png`, `before-768px.png`, `before-390px.png`, `before-320px.png`.
- `tests-passing.log`: complete existing-suite output.

**Approval requested:** approve or revise the phased plan and authorize **Stage 1 only**. Every subsequent stage remains subject to separate explicit approval.
