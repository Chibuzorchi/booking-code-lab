"""HTTP client for sportybet's share-code lookup.

GET https://www.sportybet.com/api/ng/orders/share/<CODE> resolves a booking
(share) code to its selections with no auth. Envelope:
    {"bizCode": 10000, "message": "...", "data": {...}}   # 10000 = OK
    {"bizCode": 19000, "message": "The code is invalid."} # bad/expired code
"""
from __future__ import annotations

import requests

from engine.coupon import Coupon
from engine.archive import archive_response
from engine.logger import get_logger
from infra.config.settings import Settings
from infra.parser import parse_share

BIZ_OK = 10000


class ShareApiClient:
    def __init__(self, settings: Settings, logger=None):
        self.settings = settings
        self.logger = logger or get_logger("share-api")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Referer": settings.site_url,
            }
        )

    def _url(self, code: str) -> str:
        return f"{self.settings.share_api_base}/{code}"

    def fetch_raw(self, code: str) -> dict | None:
        try:
            resp = self.session.get(self._url(code), timeout=self.settings.request_timeout_s)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as err:
            self.logger.debug(f"fetch failed for {code}: {err}")
            return None

    def decode(self, code: str) -> Coupon:
        payload = self.fetch_raw(code)
        observed_at, raw_path = archive_response(self.settings.results_dir,
                                                 self.settings.provider or "sportybet", code, payload)
        if payload is None:
            return Coupon(code=code, ok=False, error="TRANSPORT_ERROR",
                          provider=self.settings.provider or "sportybet")
        coupon = parse_share(code, payload, provider=self.settings.provider or "sportybet")
        coupon.observed_at = observed_at
        coupon.raw_response_path = raw_path
        return coupon
