# Harvest / Distinct / De-correlate UI audit

The main failure was hierarchy: unrelated inputs and actions competed in one wrapping row, while implementation details occupied the same visual level as results. The refactor groups decisions, reserves filled buttons for primary actions, and keeps evidence available through disclosure.

The design follows Apple's [layout guidance](https://developer.apple.com/design/human-interface-guidelines/layout): group related controls, give essential information sufficient space, and disclose secondary content progressively. The requested 44 × 44 **CSS pixel** minimum is enforced for this web app; CSS pixels are not interchangeable with native platform points.

## 1. Codebase findings

| File / selector or function | Original problem | Implemented fix |
| --- | --- | --- |
| `portfolio/app.html`: `#s1-fields > .grid` | Provider, seed, fresh booking, three limits and two actions shared a single flex row; wrapping changed their visual relationships. | Named source and limit sections; two-column source row and three-column limit row; separate action bar; one column on narrow screens. |
| `portfolio/app.html`: `.grid`, `.panel`, `.bar`, `.facts` | Competing 7/9/10/14/18/22px spacing, bordered counters and nested bordered panels obscured structure. | Shared 4/8/12/16/24/32px spacing tokens; neutral surfaces; quiet unboxed metrics; one separator between result rows. |
| `portfolio/app.html`: `#runs`, `refreshRuns()` | Native dropdown exposed raw run IDs concatenated with status and seed. No search or readable field hierarchy. | Searchable native single-select list, bounded height, short Date · Status · Seed options; selected metadata uses a definition list with the complete run ID. Search uses structured response fields, including the full ID, rather than parsing display strings. |
| `portfolio/app.html`: `#runs-load`, `#runs-check-results` | Actions could appear enabled without a selected run. | Actions remain disabled while loading, with no selection, during submission, or as applicable during a result job. Filtering away a selection clears its metadata and disables its actions. |
| `portfolio/app.html`: `#historical-results .grid` | Open/check/refresh/cancel and partial-history controls competed at the same level. | Primary “Check all results”, secondary “Open saved results” and “Cancel check”; refresh and partial history moved into closed “More result options”. |
| `portfolio/app.html`: `#results-summary`, `#results-rates`, `#results-evidence`, `loadResultPage()` | Counts, percentages, revisions, source limitations and leg outcomes formed long text walls. | Short population summary, outcome totals, labeled percentage metrics with numerator/denominator, and closed coverage/source evidence. Population and revision remain independent of pagination and filtering. |
| `portfolio/app.html`: historical result rendering | Ticket ID and status were combined in a heading; selection details and provenance were continuous paragraphs. | Separate status badge; selection metadata grid; nested source/matching disclosure with fixture ID, observed time, match tier and rule. Original evidence is retained. |
| `portfolio/app.html`: `label.check input`, `details.more > summary` | Checkbox measured 18px; disclosure summaries had no minimum height. | Checkbox hit box is 44px with a 20px visible mark; entire label remains clickable. Summaries have a 44px minimum. Keyboard focus and forced-colors support are retained. |
| `portfolio/reporting.html`: `button,.button` | Minimum height was 42px; duplicate styles drifted from the console. | Shared 44px target rules, consistent typography and spacing, reduced row borders. |
| `portfolio/distinct_page.py`: `.copy`, `.allbtn` | Padding alone did not guarantee minimum target dimensions. | Shared target rules and responsive layout; detail tables scroll within their container. |
| All templates: disabled button rules | `cursor:default` suggested normal interaction; hover rules were not consistently scoped. | Reduced opacity, `cursor:not-allowed`, no hover filter for disabled controls; hover feedback applies to enabled buttons. |
| `portfolio/pages.py` | Each generated page maintained its own foundational CSS. | One `ui.css`, inlined into all three templates. Standalone exports continue working over `file://`. |

## 2. Prioritized punch list

All items below are implemented.

- **P0 — Control usability:** enforce 44 × 44px controls, visible keyboard focus, clear disabled states, preserved native keyboard behavior and no narrow-screen page overflow.
- **P0 — Reliable selection:** prevent actions on an empty/loading/filtered-away run selection; preserve existing provider and stale-response guards.
- **P1 — Form hierarchy:** separate source, limits, advanced settings and scan actions; collapse advanced settings by default.
- **P1 — Action hierarchy:** use one filled primary action in each action group; demote refresh and partial history into result options; use verb-first action labels.
- **P1 — Run browsing:** filter by date/status/seed/ID, shorten visible options, expose complete metadata only for the selected run.
- **P1 — Results readability:** separate population counts, rates, status and evidence; preserve denominators and explanations of unresolved outcomes.
- **P2 — Visual consistency:** shared spacing and type scale, quiet metrics, fewer borders, neutral header, responsive layouts and reduced-motion rules.

## 3. Implementation

The changes are already applied. [UI_REFACTOR.patch](UI_REFACTOR.patch) contains the exact unified code/CSS/test diffs, relative to the working tree at the start of this audit. It excludes unrelated pre-existing backend and historical-results work. Do not apply it again to the current working tree.

The implementation files are:

- [Shared CSS](../portfolio/ui.css): spacing tokens, typography, minimum targets, focus, disabled states, responsive foundations.
- [Console](../portfolio/app.html): form sections, action groups, searchable run list, metrics and evidence disclosure.
- [Asset inlining](../portfolio/pages.py): `ui_css()` and shared placeholder replacement.
- [Standalone report](../portfolio/reporting.html) and [Distinct export](../portfolio/distinct_page.py): shared foundation with only template-specific layout rules.
- [Page tests](../tests/test_pages.py) and [console probe](../tests/fixtures/console_probe.js): run-search coverage and realistic selection-change events.

The run browser deliberately retains a native single-select list. This provides arrow-key selection, focus behavior and platform semantics without implementing a custom ARIA combobox. CSS controls its visible row size where supported; native rendering still warrants checking in Safari and on actual iOS devices before release.

## Validation

- 37 page/script/clipboard/state tests pass, including search by seed, provider filtering, readable options, complete selected metadata, empty search, disabled actions and retained historical-result revision/population behavior.
- 8 report tests pass.
- 23 server tests pass.
- Browser checks in Chromium at 1280px, 375px and 320px: populated historical results, expanded selection/source evidence, standalone Distinct and standalone report. No horizontal page overflow or visible control under 44 × 44px in those checked states.
- Browser interaction checks: run filtering and selection, open-run/open-results actions, fresh-code checkbox, and nested evidence disclosure. Historical rate cards retain their denominators.
- Browser data was intercepted with fixtures. No provider scan or live results retrieval was initiated. This is UI validation, not a claim about live data correctness or exhaustive HIG conformance.
- `git diff --check` passes.
