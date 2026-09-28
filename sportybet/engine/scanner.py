"""The scan engine: mutate a seed booking code, decode each variant via a
provider client, dedupe, and collect coupons whose total odds clear a threshold.

Provider-agnostic: it drives any client exposing ``decode(code) -> Coupon`` and
reads scan knobs from any BaseSettings. bet9ja and sportybet share this file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Protocol

from engine.event_policy import coupon_simulation_reason
from engine.code_mutator import mutate
from engine.coupon import Coupon
from engine.logger import get_logger


class CouponClient(Protocol):
    def decode(self, code: str) -> Coupon: ...


# Stop reasons: why the candidate loop ended. "cancelled"/"failure" are
# recorded by the run registry, not by the scanner (the worker never survives
# its own cancellation to save).
STOP_QUALIFYING_LIMIT = "qualifying_limit"
STOP_TRY_BUDGET = "try_budget"
STOP_CANDIDATES_EXHAUSTED = "candidates_exhausted"
PROGRESS_INTERVAL_S = 0.5


@dataclass
class ScanResult:
    seed: str
    tried: int = 0
    found_ok: int = 0
    excluded_simulations: int = 0
    qualifying: list[Coupon] = field(default_factory=list)
    effective_min_odds: float | None = None
    # Canonical eligibility basis end to end: the product of the recorded
    # leg prices. Site-displayed odds never gate qualification; they are
    # preserved separately in the export for reference.
    odds_basis: str = "parsed_leg_product"
    unpriced: int = 0
    stop_reason: str | None = None

    def best(self) -> Coupon | None:
        return max(self.qualifying, key=lambda c: c.parsed_leg_product, default=None)


class CouponScanner:
    def __init__(self, client: CouponClient, settings, logger=None):
        self.client = client
        self.settings = settings
        self.logger = logger or get_logger("scanner")
        self._last_progress = 0.0

    def _progress_path(self) -> Path:
        return self.settings.results_dir / "progress.json"

    def _write_progress(self, result: ScanResult, force: bool = False) -> None:
        """Structured live counters (atomic, throttled) for the run registry.

        Never scraped from logs: this file is the worker's own progress
        channel, and only the worker writes it.
        """
        now = time.monotonic()
        if not force and now - self._last_progress < PROGRESS_INTERVAL_S:
            return
        self._last_progress = now
        payload = {
            "schema_version": 1,
            "phase": "scanning",
            "tried": result.tried,
            "found_ok": result.found_ok,
            "qualifying": len(result.qualifying),
            "excluded_simulations": result.excluded_simulations,
            "unpriced": result.unpriced,
            "stop_reason": result.stop_reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path = self._progress_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".progress.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def scan(self, seed: str, min_total_odds: float | None = None) -> ScanResult:
        min_odds = self.settings.min_total_odds if min_total_odds is None else min_total_odds
        result = ScanResult(seed=seed, effective_min_odds=min_odds)
        seen_fingerprints: set[frozenset[str]] = set()
        self._last_progress = 0.0
        self._write_progress(result, force=True)

        seed_coupon = self.client.decode(seed)
        if seed_coupon.ok:
            seen_fingerprints.add(seed_coupon.fingerprint)
            self.logger.info(
                f"Seed {seed}: {seed_coupon.num_legs} legs, "
                f"leg-product odds {seed_coupon.parsed_leg_product}"
            )

        candidates = mutate(seed, self.settings.max_mutation_depth, self.settings.charset)
        delay = self.settings.request_delay_ms / 1000.0

        def worker(code: str) -> Coupon:
            if delay:
                time.sleep(delay)
            return self.client.decode(code)

        with ThreadPoolExecutor(max_workers=self.settings.request_workers) as pool:
            in_flight: dict = {}
            exhausted = False
            while not exhausted or in_flight:
                while (
                    not exhausted
                    and len(in_flight) < self.settings.request_workers
                    and result.tried < self.settings.max_codes_to_try
                ):
                    try:
                        code = next(candidates)
                    except StopIteration:
                        exhausted = True
                        break
                    in_flight[pool.submit(worker, code)] = code
                    result.tried += 1

                if result.tried >= self.settings.max_codes_to_try:
                    exhausted = True
                if not in_flight:
                    break

                done = next(as_completed(in_flight))
                in_flight.pop(done)
                coupon = done.result()
                self._write_progress(result)
                if not coupon.ok:
                    continue
                # sportybet serves expired coupons forever; keep only still-bookable
                # ones (all legs upcoming) so the scan behaves like bet9ja's expiry.
                if not getattr(coupon, "available", True):
                    continue
                result.found_ok += 1
                reason = coupon_simulation_reason(coupon)
                if reason:
                    result.excluded_simulations += 1
                    self.logger.info(f"Skipping {coupon.code}: {reason}")
                    continue
                if coupon.fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(coupon.fingerprint)
                odds = coupon.parsed_leg_product
                legs = coupon.num_legs
                if odds is None:
                    result.unpriced += 1
                    self.logger.info(
                        f"Skipping {coupon.code}: no finite leg-product odds "
                        "(missing/malformed leg prices or product overflow)")
                    continue
                max_odds = getattr(self.settings, "max_total_odds", 0.0)
                min_legs = getattr(self.settings, "min_legs", 0)
                max_legs = getattr(self.settings, "max_legs", 0)
                if (odds >= min_odds
                        and (max_odds <= 0 or odds <= max_odds)
                        and (min_legs <= 0 or legs >= min_legs)
                        and (max_legs <= 0 or legs <= max_legs)):
                    result.qualifying.append(coupon)
                    self.logger.success(
                        f"HIT {coupon.code}: {coupon.num_legs} legs @ "
                        f"leg-product {coupon.parsed_leg_product}"
                    )
                    if self.settings.max_qualifying and len(result.qualifying) >= self.settings.max_qualifying:
                        result.stop_reason = STOP_QUALIFYING_LIMIT
                        exhausted = True
                        break
                self._write_progress(result)

        if result.stop_reason is None:
            if (self.settings.max_qualifying
                    and len(result.qualifying) >= self.settings.max_qualifying):
                result.stop_reason = STOP_QUALIFYING_LIMIT
            elif result.tried >= self.settings.max_codes_to_try:
                result.stop_reason = STOP_TRY_BUDGET
            else:
                result.stop_reason = STOP_CANDIDATES_EXHAUSTED
        self._write_progress(result, force=True)

        self.logger.info(
            f"Scan done: tried {result.tried}, valid {result.found_ok}, "
            f"qualifying (>= {min_odds}) {len(result.qualifying)}, "
            f"simulation coupons excluded {result.excluded_simulations}, "
            f"stop reason {result.stop_reason}"
        )
        return result

    def save(self, result: ScanResult) -> dict[str, Path]:
        """Persist into results_dir/codes (codes only) and results_dir/extracts (full)."""
        codes_dir = self.settings.results_dir / "codes"
        extracts_dir = self.settings.results_dir / "extracts"
        codes_dir.mkdir(parents=True, exist_ok=True)
        extracts_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:12]
        eligible = [c for c in result.qualifying if not coupon_simulation_reason(c)]
        excluded_at_save = len(result.qualifying) - len(eligible)
        ranked = sorted(eligible, key=lambda c: c.parsed_leg_product, reverse=True)
        top = getattr(self.settings, "top_n", 0)
        if top and top > 0:
            ranked = ranked[:top]

        codes_path = codes_dir / f"scan_{result.seed}_{stamp}.txt"
        with codes_path.open("x", encoding="utf-8") as stream:
            stream.write("".join(f"{c.code} │ {c.num_legs} │ {c.parsed_leg_product:,.2f}\n" for c in ranked))

        extract_path = extracts_dir / f"scan_{result.seed}_{stamp}.json"
        extract_tmp = extracts_dir / f".tmp-{stamp}"
        with extract_tmp.open("w", encoding="utf-8") as stream:
            json.dump({
            "provider": getattr(self.settings, "provider", ""),
            "seed": result.seed,
            "tried": result.tried,
            "found_ok": result.found_ok,
            "excluded_simulations": result.excluded_simulations + excluded_at_save,
            "unpriced_excluded": result.unpriced,
            "simulation_filter": "Z. / SRL / simulated / simulation / virtual labels",
            "odds_basis": result.odds_basis,
            "stop_reason": result.stop_reason,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "min_total_odds": result.effective_min_odds if result.effective_min_odds is not None else self.settings.min_total_odds,
            "max_total_odds": self.settings.max_total_odds,
            "min_legs": self.settings.min_legs,
            "max_legs": self.settings.max_legs,
            "top_n_display": self.settings.top_n,
            "max_qualifying": self.settings.max_qualifying,
            "max_codes_to_try": self.settings.max_codes_to_try,
            "max_mutation_depth": self.settings.max_mutation_depth,
            "request_workers": self.settings.request_workers,
            "qualifying": [c.as_dict() for c in sorted(eligible, key=lambda c: c.code)],
        }, stream, indent=2, allow_nan=False)
        os.replace(extract_tmp, extract_path)

        self.logger.info(
            f"Saved {len(ranked)} code(s) -> {codes_path}  |  full extract -> {extract_path}"
        )
        paths = {"codes": codes_path, "extract": extract_path}
        # Keep the shared presentation layer outside the provider packages.
        # Evidence has already been saved: a presentation failure must not lose it.
        analysis_root = Path(__file__).resolve().parents[2] / "ticket-analysis"
        try:
            subprocess.run(
                [sys.executable, "-m", "portfolio.reporting", "--provider", "sportybet",
                 "--results", str(self.settings.results_dir.resolve())],
                cwd=analysis_root, check=True, capture_output=True, text=True, timeout=60,
            )
            paths["report"] = self.settings.results_dir / "index.html"
            self.logger.info(f"Open results report -> {paths['report']}")
        except (OSError, subprocess.SubprocessError) as exc:
            self.logger.warning(f"Scan files saved, but the report could not refresh: {exc}")
        return paths
