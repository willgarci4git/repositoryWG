#!/usr/bin/env python3
"""
Builds a static HTML dashboard (docs/index.html) consolidating:
  - Ofertas detectadas nos últimos 7 dias ("da semana")
  - Ofertas detectadas nos últimos 30 dias ("do mês")
  - Histórico completo de 60 dias corridos (ofertas + menções editoriais)
  - Um resumo por rota (menor preço visto, última checagem)

Reads config.template.json (route labels — no secrets in it) and
price_history.json (the rolling state check_flights.py maintains).
Run AFTER check_flights.py, in the same GitHub Actions job, so the
dashboard is always built from the same run's fresh data.

Pure Python stdlib — no dependencies, no client-side JS/fetch (GitHub
Pages serves this as a fully static, pre-rendered file).
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_TEMPLATE_PATH = os.path.join(BASE_DIR, "config.template.json")
HISTORY_PATH = os.path.join(BASE_DIR, "price_history.json")
OUT_DIR = os.path.join(BASE_DIR, "docs")
OUT_PATH = os.path.join(OUT_DIR, "index.html")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def fmt_price(v):
    if v is None:
        return "—"
    return f"R$ {v:,.0f}".replace(",", ".")


def within_days(date_str, days, today):
    try:
        d = datetime.fromisoformat(date_str).date()
    except (ValueError, TypeError):
        return False
    return (today - d).days <= days


def main():
    cfg = load_json(CONFIG_TEMPLATE_PATH, {"routes": []})
    history = load_json(HISTORY_PATH, {})
    routes_by_id = {r["id"]: r for r in cfg.get("routes", [])}

    today = datetime.now(timezone.utc).date()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    deals_log = history.get("_deals_log", [])
    mentions_log = history.get("_mentions_log", [])

    deals_7 = [d for d in deals_log if within_days(d.get("logged_at"), 7, today)]
    deals_30 = [d for d in deals_log if within_days(d.get("logged_at"), 30, today)]
    deals_60 = [d for d in deals_log if within_days(d.get("logged_at"), 60, today)]
    mentions_60 = [m for m in mentions_log if within_days(m.get("logged_at"), 60, today)]

    deals_7.sort(key=lambda d: d.get("logged_at", ""), reverse=True)
    deals_30.sort(key=lambda d: d.get("logged_at", ""), reverse=True)
    deals_60.sort(key=lambda d: d.get("logged_at", ""), reverse=True)
    mentions_60.sort(key=lambda m: m.get("logged_at", ""), reverse=True)

    # Per-route summary: latest observation + cheapest seen in last 60 days
    route_rows = []
    for rid, route in routes_by_id.items():
        r_hist = history.get(rid, {})
        obs = [o for o in r_hist.get("observations", []) if within_days(o.get("checked_at"), 60, today)]
        if not obs:
            continue
        obs_sorted = sorted(obs, key=lambda o: o.get("checked_at", ""))
        latest = obs_sorted[-1]
        cheapest = min(obs, key=lambda o: o["price"])
        route_rows.append({
            "label": route["label"],
            "latest_price": latest["price"],
            "latest_date": latest["checked_at"],
            "cheapest_price": cheapest["price"],
            "cheapest_date": cheapest.get("date"),
            "verified_nonstop": route.get("verified_nonstop", False),
        })
    route_rows.sort(key=lambda r: r["label"])

    def deal_row(d):
        price = d.get("serpapi_confirmed_price_brl") or d.get("travelpayouts_price_brl")
        confirmed = d.get("serpapi_confirmed_price_brl") is not None
        badge = '<span class="tag tag-ok">confirmado</span>' if confirmed else '<span class="tag tag-warn">estimado</span>'
        return (
            f"<tr><td>{html.escape(d.get('logged_at',''))}</td>"
            f"<td>{html.escape(d.get('label',''))}</td>"
            f"<td>{fmt_price(price)} {badge}</td>"
            f"<td>{html.escape(d.get('outbound_date',''))} → {html.escape(d.get('return_date',''))}</td>"
            f"<td>{html.escape(d.get('reason',''))}</td>"
            f"<td><a href=\"{html.escape(d.get('link','#'))}\" target=\"_blank\" rel=\"noopener\">ver</a></td></tr>"
        )

    def mention_row(m):
        price = f"a partir de {fmt_price(int(m['price_hint_brl'].replace('.', '')))}" if m.get("price_hint_brl") else "—"
        return (
            f"<tr><td>{html.escape(m.get('logged_at',''))}</td>"
            f"<td>{html.escape(m.get('label',''))}</td>"
            f"<td>{html.escape(price)}</td>"
            f"<td>{html.escape(m.get('title',''))}</td>"
            f"<td><a href=\"{html.escape(m.get('link','#'))}\" target=\"_blank\" rel=\"noopener\">ver</a></td></tr>"
        )

    def route_row(r):
        stops_note = "" if r["verified_nonstop"] else ' <span class="tag tag-muted">c/ conexão</span>'
        return (
            f"<tr><td>{html.escape(r['label'])}{stops_note}</td>"
            f"<td>{fmt_price(r['latest_price'])} <span class=\"muted\">({html.escape(r['latest_date'])})</span></td>"
            f"<td>{fmt_price(r['cheapest_price'])} <span class=\"muted\">({html.escape(str(r['cheapest_date']))})</span></td></tr>"
        )

    deals_7_html = "\n".join(deal_row(d) for d in deals_7) or '<tr><td colspan="6" class="empty">Nenhuma queda detectada nos últimos 7 dias.</td></tr>'
    deals_30_html = "\n".join(deal_row(d) for d in deals_30) or '<tr><td colspan="6" class="empty">Nenhuma queda detectada nos últimos 30 dias.</td></tr>'
    deals_60_html = "\n".join(deal_row(d) for d in deals_60) or '<tr><td colspan="6" class="empty">Nenhuma queda detectada nos últimos 60 dias.</td></tr>'
    mentions_html = "\n".join(mention_row(m) for m in mentions_60) or '<tr><td colspan="5" class="empty">Nenhuma menção nova nos últimos 60 dias.</td></tr>'
    routes_html = "\n".join(route_row(r) for r in route_rows) or '<tr><td colspan="3" class="empty">Sem dados ainda.</td></tr>'

    usage = history.get("_serpapi_usage", {})

    page = f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de Preços de Voos</title>
<style>
  :root {{
    --bg: #f7f7f8; --card: #ffffff; --text: #1a1a1a; --muted: #6b7280;
    --border: #e5e7eb; --accent: #2563eb;
    --tag-ok-bg: #dcfce7; --tag-ok-text: #166534;
    --tag-warn-bg: #fef3c7; --tag-warn-text: #92400e;
    --tag-muted-bg: #e5e7eb; --tag-muted-text: #4b5563;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0f1115; --card: #1a1d24; --text: #e5e7eb; --muted: #9ca3af;
      --border: #2a2e37; --accent: #60a5fa;
      --tag-ok-bg: #14532d; --tag-ok-text: #bbf7d0;
      --tag-warn-bg: #78350f; --tag-warn-text: #fde68a;
      --tag-muted-bg: #374151; --tag-muted-text: #d1d5db;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 24px 16px 64px;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px; margin-bottom: 20px; overflow-x: auto;
  }}
  .card h2 {{ font-size: 1.05rem; margin: 0 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
  td:nth-child(4), td:nth-child(5) {{ white-space: normal; }}
  tr:last-child td {{ border-bottom: none; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .muted {{ color: var(--muted); font-size: 0.8em; }}
  .empty {{ text-align: center; color: var(--muted); padding: 20px; white-space: normal; }}
  .tag {{ display: inline-block; font-size: 0.7rem; padding: 2px 6px; border-radius: 999px; font-weight: 600; }}
  .tag-ok {{ background: var(--tag-ok-bg); color: var(--tag-ok-text); }}
  .tag-warn {{ background: var(--tag-warn-bg); color: var(--tag-warn-text); }}
  .tag-muted {{ background: var(--tag-muted-bg); color: var(--tag-muted-text); }}
  .footer {{ color: var(--muted); font-size: 0.75rem; text-align: center; margin-top: 32px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>✈️ Monitor de Preços de Voos</h1>
  <div class="subtitle">Gerado em {generated_at} · SerpAPI: {usage.get('count', 0)}/{usage.get('month', '')} este mês</div>

  <div class="card">
    <h2>🔥 Ofertas da semana (últimos 7 dias)</h2>
    <table>
      <tr><th>Data</th><th>Destino</th><th>Preço</th><th>Datas</th><th>Motivo</th><th></th></tr>
      {deals_7_html}
    </table>
  </div>

  <div class="card">
    <h2>📅 Ofertas do mês (últimos 30 dias)</h2>
    <table>
      <tr><th>Data</th><th>Destino</th><th>Preço</th><th>Datas</th><th>Motivo</th><th></th></tr>
      {deals_30_html}
    </table>
  </div>

  <div class="card">
    <h2>📰 Menções editoriais — Melhores Destinos (60 dias)</h2>
    <table>
      <tr><th>Data</th><th>Destino</th><th>Preço (aprox.)</th><th>Post</th><th></th></tr>
      {mentions_html}
    </table>
  </div>

  <div class="card">
    <h2>📊 Resumo por rota (últimos 60 dias)</h2>
    <table>
      <tr><th>Destino</th><th>Preço mais recente</th><th>Menor preço visto</th></tr>
      {routes_html}
    </table>
  </div>

  <div class="card">
    <h2>🗂️ Histórico completo de ofertas (60 dias)</h2>
    <table>
      <tr><th>Data</th><th>Destino</th><th>Preço</th><th>Datas</th><th>Motivo</th><th></th></tr>
      {deals_60_html}
    </table>
  </div>

  <div class="footer">Gerado automaticamente pelo GitHub Actions · <a href="https://github.com/willgarci4git/repositoryWG" target="_blank" rel="noopener">repositoryWG</a></div>
</div>
</body>
</html>
"""

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {OUT_PATH} ({len(page)} bytes)")


if __name__ == "__main__":
    main()
