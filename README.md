# Sport workspace

Provider tools, ticket analysis, and an independent upstream sports-data library.

```text
Sport/
├── sportybet/          # SportyBet booking, scanning, and provider results
├── bet9ja/             # Bet9ja booking, scanning, and provider results
├── sports-skills/      # Independent Git checkout connected to upstream
├── ticket-analysis/    # Shared comparison and selection project
│   ├── portfolio/      # Python package and CLI
│   ├── configs/        # Example analysis profiles
│   ├── tests/          # Offline analysis and provider integrity tests
│   ├── docs/           # Designs, roadmap, and relocation notes
│   └── reports/        # Local analysis runs and saved inputs
└── archive/
    └── gamble/         # Earlier research specifications and agent skills
```

## Run the projects

Each example below starts from `Sport/`. Run provider commands from their own
directories: they use the same Python package names and relative results paths.

| Project | Instructions |
| --- | --- |
| SportyBet | [Setup and commands](sportybet/README.md) |
| Bet9ja | [Setup and commands](bet9ja/README.md) |
| Ticket analysis | [Profiles, comparison, selection, and reports](ticket-analysis/README.md) |
| Sports skills | [Upstream project's documentation](sports-skills/README.md) |

Offline comparison of existing extracts:

```sh
cd ticket-analysis
python3 -m portfolio --from ../sportybet/results/extracts ../bet9ja/results/extracts --output reports/first-run
```

Use a new output directory for each run. Selection is available with `--select
--max-exposure K`, where K is the maximum number of selected codes sharing a
selection identity. See `python3 -m portfolio --help` for the complete interface.

Offline tests (Python 3.10+; provider integrity tests also need `python-dotenv`):

```sh
cd ticket-analysis
python3 -m unittest discover -s tests -v
```

## Read provider results

- **Bet9ja:** [browser report](bet9ja/results/index.html) · [Text scan index](bet9ja/results/START_HERE.txt)
- **SportyBet:** [browser report](sportybet/results/index.html) · [Text scan index](sportybet/results/START_HERE.txt)

Open the HTML file in a browser; no server is needed. Reports refresh when a scan
is saved, and the Text index links to a readable summary for each scan.
These locally generated files remain ignored alongside the provider results.

## Independent sports-skills checkout

`sports-skills/` remains connected to
<https://github.com/machina-sports/sports-skills.git> with its own Git history.
The root `.gitignore` excludes it so a future parent repository does not
accidentally record an unmanaged nested repository. This cleanup does not
initialize a parent repository or upgrade sports-skills.

Consequently, a future clone of the parent repository alone will not include
this library. Recreate the independent checkout with:

```sh
git clone https://github.com/machina-sports/sports-skills.git sports-skills
```

Review updates separately using `git -C sports-skills fetch origin` and
`git -C sports-skills log HEAD..origin/main --oneline`. A fresh clone uses current
upstream; the checkout retained during this reorganization is commit `677f840`.
If the parent repository should later pin and automatically describe this
dependency, it can be explicitly registered as a submodule.

## Local files and historical material

Provider `.env` files, virtual environments, caches, provider `results/`, and
analysis `reports/` are local and ignored. `.env.example` templates may be tracked.
Keep backup copies of local results separately from source control.

The original analysis README moved to `ticket-analysis/README.md`. Root `design/`
moved to `ticket-analysis/docs/`; root `gamble/` moved intact to `archive/gamble/`.
Historical report contents were preserved, including recorded original paths.
See [relocation details](ticket-analysis/docs/README.md).
