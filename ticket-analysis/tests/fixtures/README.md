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

`api_football_ipswich_nottingham_forest_fulltime.json` is one real, unmodified
response item from a live, authorized `GET /fixtures?date=2026-10-23` call against
api-sports.io made 24 October 2026 (Ipswich 1-1 Nottingham Forest, Premier League),
used to verify `sources/api_football.py`'s adapter (`build_fixture`,
`map_fixture_state`, `filter_in_scope`) against a real response shape rather than an
assumed one. One request of the account's 100/day free-tier quota.
