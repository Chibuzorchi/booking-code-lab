"""HTTP client for bet9ja's book-a-bet coupon API.

GetBookABetCouponV2 resolves a booking code to its full selection list with a
plain unauthenticated GET, so we decode/scan codes far faster than by driving
the betslip UI. Payload parsing lives in infra.parser; the returned object is a
the vendored engine.coupon.Coupon.
"""
from __future__ import annotations

import requests

from engine.coupon import Coupon
from engine.archive import archive_response
from engine.logger import get_logger
from infra.config.settings import Settings
from infra.parser import parse_coupon


class CouponApiClient:
    def __init__(self, settings: Settings, logger=None):
        self.settings = settings
        self.logger = logger or get_logger("coupon-api")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": settings.sports_url,
                "X-Requested-With": "XMLHttpRequest",
            }
        )

    def _url(self, code: str) -> str:
        return (
            f"{self.settings.coupon_api_base}/GetBookABetCouponV2"
            f"?couponCode={code}&v_cache_version={self.settings.cache_version}"
        )

    def fetch_raw(self, code: str) -> dict | None:
        """Return the raw JSON payload for a code, or None on transport failure."""
        try:
            resp = self.session.get(self._url(code), timeout=self.settings.request_timeout_s)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            self.logger.debug(f"fetch failed for {code}: {err}")
            return None

    def decode(self, code: str) -> Coupon:
        """Resolve a booking code to a Coupon (ok=False if it doesn't exist)."""
        payload = self.fetch_raw(code)
        observed_at, raw_path = archive_response(self.settings.results_dir,
                                                 self.settings.provider or "bet9ja", code, payload)
        if payload is None:
            return Coupon(code=code, ok=False, error="TRANSPORT_ERROR",
                          provider=self.settings.provider or "bet9ja")
        coupon = parse_coupon(code, payload, provider=self.settings.provider or "bet9ja")
        coupon.observed_at = observed_at
        coupon.raw_response_path = raw_path
        return coupon
