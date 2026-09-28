# Provider regression fixture provenance

`sportybet_setka_native.json` is a minimized fixture derived from the authorized
read-only response for N5PZDF on 2026-09-21, stored locally under
`reports/correctness-refresh-network/raw/N5PZDF.json`. The event/market/specifier/
outcome identifiers and field shapes are retained; names and share code are replaced.
Only one event/reference is retained. Stake, account and unrelated ticket data are
omitted. It demonstrates a real opaque event ID, not statistical or payout validity.

Bet9ja parser/export and dropped-leg regressions use synthetic payloads in
`test_provider_integrity.py`. No live Bet9ja retrieval was authorized in this slice;
those tests must not be described as representative live-data coverage.
