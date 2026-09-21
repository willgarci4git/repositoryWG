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


# ---------------------------------------------------------------------------
# Apps/portais de viagem — não são fonte de dado do monitor (não têm API
# aberta pra alimentar detecção de preço), só atalhos + referência rápida do
# que cada um faz e onde entra no fluxo de planejar/comprar/viajar.
# name, url (None = identidade não confirmada, não linkar), category, what, use
# ---------------------------------------------------------------------------
TRAVEL_TOOLS = [
    ("Sherpa", "https://apply.joinsherpa.com/travel-restrictions?language=pt-BR", "Documentação",
     "Exigência de visto/vacina/documento por nacionalidade e destino",
     "Checar antes de fechar uma viagem internacional achada aqui"),
    ("BestOnwardTicket", "https://bestonwardticket.com", "Documentação",
     "Reserva provisória de passagem (prova de saída p/ visto)",
     "Só se pedirem prova de retorno pra emitir visto"),
    ("seats.aero", "https://seats.aero", "Passagens & milhas",
     "Disponibilidade de assento por milhas/pontos",
     "Alertas nativos e grátis do próprio site (fora deste painel)"),
    ("Skiplagged", "https://skiplagged.com", "Passagens & milhas",
     "Busca de voos com tarifas \"hidden-city\"",
     "Conferência manual pontual — não automatizado (ToS proíbe, risco de cancelamento de trecho)"),
    ("Comparemania", "https://www.comparemania.com.br", "Passagens & milhas",
     "Cashback e comparação de troca de milhas (BR)",
     "Conferência manual de cashback antes de comprar"),
    ("Hopper", "https://www.hopper.com", "Passagens & milhas",
     "Previsão de preço de voo/hotel, app próprio",
     "Só uso manual — não integrável (API é B2B fechada pra bancos/companhias, não pra terceiros)"),
    ("AirHopping", "https://www.airhopping.com", "Passagens & milhas",
     "Busca voos multi-trecho baratos (estilo volta ao mundo)",
     "Itinerários com várias paradas fora do padrão ida-e-volta"),
    ("Omio", "https://www.omio.com", "Passagens & milhas",
     "Busca e compra de trem/ônibus/voo (forte na Europa)",
     "Comparar trem vs. voo em trechos intra-Europa"),
    ("GigSky", "https://www.gigsky.com", "Conectividade (eSIM)",
     "eSIM de dados internacional",
     "Comprar internet antes de embarcar"),
    ("Airalo", "https://www.airalo.com", "Conectividade (eSIM)",
     "eSIM de viagem — o mais conhecido do mercado",
     "Comprar chip virtual antes de embarcar"),
    ("Mobimatter", "https://www.mobimatter.com", "Conectividade (eSIM)",
     "Marketplace que compara vários provedores de eSIM",
     "Comparar preço de eSIM entre provedores"),
    ("Wikiloc", "https://www.wikiloc.com", "Mapas & trilhas",
     "Trilhas e rotas outdoor (trekking, ciclismo)",
     "Planejar trilha no destino"),
    ("maps.me", "https://maps.me", "Mapas & trilhas",
     "Mapas offline por cidade/região",
     "Navegação sem internet no destino"),
    ("AllTrails", "https://www.alltrails.com", "Mapas & trilhas",
     "Trilhas de caminhada com avaliação da comunidade",
     "Achar trilha segura e bem avaliada no destino"),
    ("CityMaps (Ulmon)", "https://www.ulmon.com", "Mapas & trilhas",
     "Mapas offline de cidades",
     "Navegação urbana sem internet"),
    ("Moovit", "https://moovit.com", "Mapas & trilhas",
     "Navegação de transporte público em tempo real",
     "Se locomover de ônibus/metrô/trem no destino"),
    ("Rome2Rio", "https://www.rome2rio.com", "Mapas & trilhas",
     "Rotas multimodais (voo+trem+ônibus+balsa) entre dois pontos",
     "Comparar como chegar de A a B por qualquer meio"),
    ("Holicay", "https://www.holicay.com", "Planejamento & atrações",
     "Planejador de viagem com IA (roteiro dia a dia)",
     "Montar roteiro colaborativo com o grupo"),
    ("Wanderlog", "https://wanderlog.com", "Planejamento & atrações",
     "Planejador de itinerário/roteiro de viagem",
     "Montar o roteiro depois que a passagem for comprada"),
    ("Go City", "https://gocity.com", "Planejamento & atrações",
     "Passe combinado de atrações turísticas",
     "Economizar em ingressos na cidade"),
    ("Atlas Obscura", "https://www.atlasobscura.com", "Planejamento & atrações",
     "Guia de lugares insólitos/curiosos",
     "Achar atração fora do óbvio no destino"),
    ("Rail Planner (Eurail)", "https://www.eurail.com/en/plan-your-trip/rail-planner-app", "Planejamento & atrações",
     "Planejador de trem pela Europa",
     "Rotas de trem entre cidades europeias"),
    ("Worldpackers", "https://www.worldpackers.com", "Planejamento & atrações",
     "Intercâmbio de trabalho por hospedagem",
     "Viagem longa/mochilão com custo reduzido"),
    ("SeatMaps", "https://seatmaps.com", "Conforto & aeroporto",
     "Mapa de assentos por aeronave/companhia",
     "Escolher poltrona depois de comprar a passagem"),
    ("LoungeBuddy", "https://www.loungebuddy.com", "Conforto & aeroporto",
     "Acesso a salas VIP de aeroporto (hoje ligado à Amex)",
     "Achar lounge disponível numa conexão"),
    ("HotelTonight", "https://www.hoteltonight.com", "Conforto & aeroporto",
     "Hotel de última hora com desconto (hoje parte do Airbnb)",
     "Reserva de emergência ou oportunista"),
    ("Bolt", "https://bolt.eu", "Transporte local",
     "App de corrida (tipo Uber, forte na Europa)",
     "Transporte local no destino"),
    ("DriveMe", "https://driveme.nl", "Transporte local",
     "Motorista particular sob demanda (predominante na Europa)",
     "Transporte de/para aeroporto em destinos específicos"),
    ("Monito", "https://www.monito.com", "Dinheiro & câmbio",
     "Comparador de câmbio e remessa internacional",
     "Ver a forma mais barata de levar/trocar dinheiro"),
    ("Ally", "https://www.ally.com", "Dinheiro & câmbio",
     "Banco americano com cartão sem tarifa internacional",
     "Opção de cartão pra gastar fora sem tarifa extra"),
    ("Yelp", "https://www.yelp.com", "Conteúdo & mídia",
     "Avaliações de restaurantes e locais",
     "Achar onde comer bem no destino"),
    ("Mult.dev", "https://mult.dev", "Conteúdo & mídia",
     "Animação de mapa de rota de viagem",
     "Criar vídeo do roteiro pra compartilhar"),
    ("GoPro Quik", "https://gopro.com/en/us/shop/quik-app-video-photo-editor", "Conteúdo & mídia",
     "Editor de vídeo automático (GoPro)",
     "Editar vídeos da viagem"),
]


def build_tools_table():
    rows = sorted(TRAVEL_TOOLS, key=lambda t: (t[2], t[0]))
    out = ['<tr><th>Categoria</th><th>App</th><th>O que é</th><th>Onde entra no seu fluxo</th></tr>']
    for name, url, category, what, use in rows:
        name_html = f'<a href="{html.escape(url)}" target="_blank" rel="noopener"><strong>{html.escape(name)}</strong></a>' if url else f'<strong>{html.escape(name)}</strong> <span class="tag tag-warn">não confirmado</span>'
        out.append(f"<tr><td>{html.escape(category)}</td><td>{name_html}</td><td>{html.escape(what)}</td><td>{html.escape(use)}</td></tr>")
    return "\n".join(out)


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
<title>Monitor de Voos WG</title>
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
  .shortcuts {{ display: flex; gap: 10px; flex-wrap: wrap; }}
  .shortcut-btn {{
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 14px; font-size: 0.85rem; font-weight: 600; color: var(--text) !important;
    text-decoration: none !important;
  }}
  .shortcut-btn:hover {{ border-color: var(--accent); }}
  .shortcut-sub {{ display: block; font-weight: 400; color: var(--muted); font-size: 0.75rem; }}
  .table-wrap td, .table-wrap th {{ white-space: normal; }}
  .table-wrap td:first-child, .table-wrap td:nth-child(2) {{ white-space: nowrap; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>✈️ Monitor de Voos WG</h1>
  <div class="subtitle">Gerado em {generated_at} · SerpAPI: {usage.get('count', 0)}/{usage.get('month', '')} este mês</div>

  <div class="card">
    <h2>🔗 Atalhos</h2>
    <div class="shortcuts">
      <a class="shortcut-btn" href="https://apply.joinsherpa.com/travel-restrictions?language=pt-BR" target="_blank" rel="noopener">
        🛂 Sherpa
        <span class="shortcut-sub">Visto/documentos por destino</span>
      </a>
      <a class="shortcut-btn" href="https://seats.aero" target="_blank" rel="noopener">
        🎫 seats.aero
        <span class="shortcut-sub">Alertas de milhas/pontos</span>
      </a>
      <a class="shortcut-btn" href="https://skiplagged.com" target="_blank" rel="noopener">
        🛫 Skiplagged
        <span class="shortcut-sub">Busca hidden-city (usar com cautela)</span>
      </a>
      <a class="shortcut-btn" href="https://seatmaps.com" target="_blank" rel="noopener">
        💺 SeatMaps
        <span class="shortcut-sub">Mapa de assentos por aeronave</span>
      </a>
      <a class="shortcut-btn" href="https://www.comparemania.com.br" target="_blank" rel="noopener">
        🪙 Comparemania
        <span class="shortcut-sub">Cashback/milhas em passagens</span>
      </a>
      <a class="shortcut-btn" href="https://bestonwardticket.com" target="_blank" rel="noopener">
        📄 BestOnwardTicket
        <span class="shortcut-sub">Reserva provisória p/ visto</span>
      </a>
      <a class="shortcut-btn" href="https://wanderlog.com" target="_blank" rel="noopener">
        🗺️ Wanderlog
        <span class="shortcut-sub">Planejador de roteiro</span>
      </a>
    </div>

    <table class="table-wrap" style="margin-top:16px;">
      {build_tools_table()}
    </table>
    <div class="muted" style="margin-top:8px;">Nenhum desses tem API pública gratuita pra alimentar a detecção de preço automaticamente — são atalhos de uso manual, complementares aos alertas.</div>
  </div>

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
