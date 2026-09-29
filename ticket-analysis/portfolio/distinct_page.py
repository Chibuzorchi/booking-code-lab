"""Render the phase-1 distinct codes for a provider as a standalone HTML page.

This is the Stage-2 view: the codes that share zero games+options with any other
code in the pool, each expandable to its games, each with a Copy button, plus a
Copy-all. Reused by the CLI here and (later) by the UI server.
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

from .contracts import odds_value
from .pages import copy_js
from .uniqueness import _bare_code, analyze_extract

PROVIDER_LABEL = {"bet9ja": "bet9ja", "sportybet": "sportybet"}


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def render_html(provider: str, report: dict) -> str:
    basis = report["odds_basis"]
    basis_label = {"parsed_leg_product": "Parsed leg product",
                   "site_displayed_odds": "Site displayed odds",
                   "verified_payout": "Verified payout"}[basis]
    def sort_key(ticket):
        value = odds_value(ticket, basis)
        return (value is None, value if value is not None else 0)
    distinct = sorted(report["distinct"], key=sort_key)
    label = PROVIDER_LABEL.get(provider, provider)
    cards = []
    for c in distinct:
        odds = odds_value(c, basis)
        code = _esc(_bare_code(c))
        legs = "".join(
            f"<tr><td>{_esc(s.get('event'))}<small>{_esc(s.get('league'))} · {_esc(s.get('kickoff'))}</small></td>"
            f"<td>{_esc(s.get('market'))}</td><td>{_esc(s.get('pick'))}</td>"
            f"<td class='n'>{_esc(round(s['odds'],2) if s.get('odds') else '—')}</td></tr>"
            for s in c["legs"])
        cards.append(
            f"""<details class="ticket"><summary>
      <span><small>Booking code</small><strong>{code}</strong></span>
      <span><small>Legs</small><span class="n">{_esc(c.get('num_legs'))}</span></span>
      <span><small>{basis_label}</small><span class="n">{_esc(f'{odds:,.2f}' if odds is not None else '—')}</span></span>
      <button class="copy" type="button" data-code="{code}" onclick="cp(this,event)">Copy</button>
      <span class="chev">›</span></summary>
      <div class="detail"><table><thead><tr><th>Match / league</th><th>Market</th><th>Pick</th><th>Odds</th></tr></thead>
      <tbody>{legs}</tbody></table></div></details>""")
    allcodes = "\n".join(_esc(_bare_code(c)) for c in distinct)
    n = len(distinct)
    lo, hi = report.get("min_odds"), report.get("max_odds")
    band = f" ({basis_label}: {lo:g}{chr(8211) + format(hi, 'g') if hi is not None else '+'})" if lo is not None else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Distinct codes · {label}</title>
<style>
:root{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#182b29;background:#f4f6f3}}*{{box-sizing:border-box}}body{{margin:0}}
header{{background:#123e35;color:#fff;padding:32px max(24px,calc((100vw - 1100px)/2))}}
.eyebrow{{text-transform:uppercase;letter-spacing:.15em;font-size:12px;font-weight:700;color:#b4d5c5}}
h1{{font-size:32px;margin:8px 0;font-weight:650}} header p{{color:#d1e1d8;max-width:720px;line-height:1.6;margin:0 0 14px}}
.bar{{display:flex;gap:10px}} button{{font:inherit;cursor:pointer}}
.allbtn{{border:1px solid #b4d5c5;background:#1c5344;color:#fff;border-radius:7px;padding:9px 14px;font-weight:600}}
main{{max-width:1100px;margin:auto;padding:24px}}
.ticket{{background:#fff;border:1px solid #dce4dc;border-radius:10px;margin-bottom:10px;overflow:hidden}}
.ticket summary{{list-style:none;cursor:pointer;display:grid;grid-template-columns:1fr 70px 130px 84px 20px;gap:14px;padding:16px;align-items:center}}
.ticket summary::-webkit-details-marker{{display:none}} .ticket summary:hover{{background:#f6f9f5}}
.ticket[open] summary{{background:#edf4ed;border-bottom:1px solid #dce4dc}}
summary small{{display:block;font-size:11px;color:#5c6c64;margin-bottom:4px}} summary strong{{font-family:ui-monospace,monospace;font-size:17px}}
.n{{font-variant-numeric:tabular-nums}} .chev{{color:#527363}} .ticket[open] .chev{{transform:rotate(90deg)}}
.copy{{border:1px solid #b8c8bd;background:#f5f8f4;border-radius:7px;padding:8px 10px;color:#184b3e;font-weight:600}}
button.is-copied{{background:#1d6b3a;color:#fff;border-color:#1d6b3a}}
button.is-copy-failed{{background:#fbe6cf;color:#8a4a0c;border-color:#bb690c}}
.copy-fallback{{display:block;margin-top:8px;width:100%;font:14px/1.4 ui-monospace,monospace;padding:8px;border:1px solid #bb690c;border-radius:6px;background:#fff8ef;color:#182b29}}
summary .copy-fallback{{grid-column:1/-1}}
.detail{{padding:8px 16px 16px}} table{{border-collapse:collapse;width:100%;text-align:left;font-size:13px}}
th{{font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:#5c6c64;background:#f7f9f5}}
th,td{{padding:11px 9px;border-bottom:1px solid #e7ece4;vertical-align:top}} td small{{display:block;color:#67796d;margin-top:4px}} td.n{{text-align:right}}
@media(max-width:640px){{.ticket summary{{grid-template-columns:1fr 84px;gap:10px}}.chev{{display:none}}}}
</style></head><body>
<header><div class="eyebrow">Phase 1 · {label}</div><h1>{n} fully distinct codes</h1>
<p>Out of {report['coupons']} eligible codes{band}, these {n} share <b>zero</b> games+options with any other code. The other {report['overlapping_codes']} overlap and are held for de-correlation.</p>
<div class="bar"><button class="allbtn" type="button" onclick="cpAll(this)">Copy all {n} codes</button></div></header>
<main>{''.join(cards)}</main>
<textarea id="all" style="position:absolute;left:-9999px" aria-hidden="true">{allcodes}</textarea>
<script>{copy_js()}</script>
<script>
// Both buttons go through the one shared helper, so per-code copy gets the same
// clipboard-then-select-the-text escalation that copy-all already had.
function cp(b,ev){{ev.preventDefault();ev.stopPropagation();Copy.button(b,b.dataset.code,{{label:'Booking code to copy'}});}}
function cpAll(b){{Copy.button(b,document.getElementById('all').value,{{copied:'All copied',label:'All distinct booking codes to copy'}});}}
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Render phase-1 distinct codes to an HTML page")
    ap.add_argument("--provider", required=True, choices=list(PROVIDER_LABEL))
    ap.add_argument("--extract", required=True, help="one run's extract JSON")
    ap.add_argument("--odds-basis", required=True,
                    choices=["parsed_leg_product", "site_displayed_odds", "verified_payout"])
    ap.add_argument("--min-odds", type=float, required=True)
    ap.add_argument("--max-odds", type=float, default=None)
    ap.add_argument("--out", default=None, help="default: <extract dir>/distinct.html")
    args = ap.parse_args()
    report = analyze_extract(args.extract, args.provider, odds_basis=args.odds_basis,
                             min_odds=args.min_odds, max_odds=args.max_odds)
    out = Path(args.out) if args.out else Path(args.extract).resolve().parent / "distinct.html"
    out.write_text(render_html(args.provider, report), encoding="utf-8")
    print(f"{report['distinct_codes']} distinct {args.provider} codes -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
