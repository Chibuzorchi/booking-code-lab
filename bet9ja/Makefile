.DEFAULT_GOAL := help
PYTHON := python3

# Overridable on the command line, e.g.  make scan CODE=5S2HFVg DEPTH=2 MIN_ODDS=5000
CODE     ?=
DEPTH    ?=
MIN_ODDS ?=
MAXCODES ?=

# Assemble optional flags only when the var is set, so .env stays the default.
SCAN_FLAGS := $(if $(DEPTH),--depth $(DEPTH),) $(if $(MIN_ODDS),--min-odds $(MIN_ODDS),) $(if $(MAXCODES),--max-codes $(MAXCODES),)
PYTEST_FLAGS := $(if $(DEPTH),--depth=$(DEPTH),) $(if $(MIN_ODDS),--min-odds=$(MIN_ODDS),) $(if $(MAXCODES),--max-codes=$(MAXCODES),)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install deps and the Chromium browser
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m playwright install chromium

# ==================== API-only runs (no browser) ====================
api: ## API run: scan from SEED_CODE (.env) or CODE=...  make api  |  make api CODE=5S2HFVg DEPTH=2
	$(PYTHON) -m scripts.scan_code $(CODE) --scan $(SCAN_FLAGS)

decode: ## Decode one code:  make decode CODE=5S2HFVg
	$(PYTHON) -m scripts.scan_code $(CODE)

scan: ## Scan a code (uses .env; override DEPTH/MIN_ODDS/MAXCODES):  make scan CODE=5S2HFVg DEPTH=2
	$(PYTHON) -m scripts.scan_code $(CODE) --scan $(SCAN_FLAGS)

scan-1: ## 1-char edit — vary last char only:  make scan-1 CODE=5S2HFVg
	$(PYTHON) -m scripts.scan_code $(CODE) --scan --depth 1 $(if $(MIN_ODDS),--min-odds $(MIN_ODDS),) $(if $(MAXCODES),--max-codes $(MAXCODES),)

scan-2: ## 2-char edit — last char then last two:  make scan-2 CODE=5S2HFVg
	$(PYTHON) -m scripts.scan_code $(CODE) --scan --depth 2 $(if $(MIN_ODDS),--min-odds $(MIN_ODDS),) $(if $(MAXCODES),--max-codes $(MAXCODES),)

scan-3: ## 3-char edit — last one, two, then three:  make scan-3 CODE=5S2HFVg
	$(PYTHON) -m scripts.scan_code $(CODE) --scan --depth 3 $(if $(MIN_ODDS),--min-odds $(MIN_ODDS),) $(if $(MAXCODES),--max-codes $(MAXCODES),)

# ==================== UI runs (browser) ====================
ui: ## UI run: book a random game via browser (prints the code)
	$(PYTHON) -m scripts.book_random

ui-scan: ## UI run: book a random game, then scan its code
	$(PYTHON) -m scripts.book_random --scan

book: ui           ## alias for `ui`
book-scan: ui-scan ## alias for `ui-scan`

test: ## Full e2e (book + scan) test; override DEPTH/MIN_ODDS:  make test DEPTH=2
	pytest tests/test_book_and_scan.py -s $(PYTEST_FLAGS)

test-scan: ## API-only scan test:  make test-scan CODE=5S2HFVg DEPTH=2
	pytest tests/test_book_and_scan.py::test_scan_from_seed -s --seed-code=$(CODE) $(PYTEST_FLAGS)

.PHONY: help install api decode scan scan-1 scan-2 scan-3 ui ui-scan book book-scan test test-scan
