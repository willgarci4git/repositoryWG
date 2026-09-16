#!/usr/bin/env python3
"""
Trade Hunter — watcher (agente financeiro informativo B3)

Roda como script simples (Bash/cron/scheduled task), sem depender do pacote
`mcp` (que exige Python 3.10+). Reaproveita sources.py, storage.py,
telegram_alert.py e market_data.py já existentes no projeto.

Modos:
  python3 watch.py --mode intraday   # checa limiares de preço + notícias novas
                                       # e manda 1 push no Telegram só se algo
                                       # relevante aconteceu (senão, silêncio)
  python3 watch.py --mode daily       # sempre manda 1 resumo diário no
                                       # Telegram: fechamento, fluxo estrangeiro
                                       # do dia e leitura de opções (liquidez)
  python3 watch.py --mode screen      # imprime um relatório completo no
                                       # terminal (sem Telegram) — usado para
                                       # responder perguntas do tipo "quais as
                                       # melhores opções pra olhar hoje?"

REGRA DE COMPLIANCE: este script nunca decide nem sugere "compre"/"venda"/
"opere". Ele só organiza dados objetivos (preço, variação, fluxo, volume de
opções, manchetes) para quem opera decidir por conta própria.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import quote as urlquote

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_data as md  # noqa: E402
from telegram_alert import send_telegram_message  # noqa: E402
from discord_alert import send_discord_message  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Negrito "de verdade" sem depender do parse_mode Markdown do Telegram — o
# Markdown legado quebra a mensagem INTEIRA se algum caractere dinâmico (ex.:
# uma manchete de notícia com "_" ou "*" sem par) aparecer em qualquer parte
# do texto (foi exatamente o bug do resumo diário). Convertendo pra Unicode
# "Sans-Serif Bold" a gente formata só o título, sem parse_mode nenhum — não
# tem como quebrar o envio, não importa o que vier no resto da mensagem.
_BOLD_MAP: dict[str, str] = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _BOLD_MAP[_c] = chr(0x1D5D4 + _i)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BOLD_MAP[_c] = chr(0x1D5EE + _i)
for _i, _c in enumerate("0123456789"):
    _BOLD_MAP[_c] = chr(0x1D7EC + _i)


def _send_to_all_channels(text: str, parse_mode: Optional[str] = "Markdown") -> dict:
    """Manda a mesma mensagem pros canais configurados (Telegram e/ou
    Discord — mesmo padrão do monitor de voos). Um canal sem `.env`
    configurado não conta como falha, só os que estão configurados e
    falharam de verdade."""
    return {
        "telegram": send_telegram_message(text, parse_mode=parse_mode),
        "discord": send_discord_message(text),
    }


def _channel_errors(results: dict) -> list[str]:
    """Filtra só falhas reais dos canais (ignora 'não configurado' — nem
    todo mundo vai usar os dois canais)."""
    errors = []
    for canal, r in results.items():
        erro = r.get("error") or ""
        if not r.get("ok") and "não configurado" not in erro:
            errors.append(f"{canal}: {erro}")
    return errors


def _bold(text: str) -> str:
    """Deixa `text` em negrito via Unicode (funciona em qualquer cliente
    Telegram, sem parse_mode). Acentos (á, ã, ç...) são decompostos em
    letra-base + acento antes de negritar a base, pra continuar legível."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(_BOLD_MAP.get(c, c) for c in decomposed)


DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "watch_state.json")
LAST_SUMMARY_FILE = os.path.join(DATA_DIR, "ultimo_resumo.md")

# Limiares de alerta intraday (variação % vs fechamento anterior, que é o que
# o TradingView já devolve em change_pct).
THRESHOLDS = {
    "MGLU3": 2.0,
    "LREN3": 2.0,
    "PRIO3": 2.0,
    "BBAS3": 2.0,
    "BRAV3": 2.0,
    "PETR4": 2.0,
    "BBSE3": 2.0,
    "ITSA4": 2.0,
    "IBOV": 1.5,
    "IBXL": 1.5,
    "VIX": 8.0,
    "BRENT": 2.0,
    "SP500": 1.0,
    # FIIs costumam variar bem menos que ações no dia a dia — limiar mais
    # sensível (1,5%) pra não deixar passar um movimento que, pra um FII, já
    # é grande.
    "ALZR11": 1.5,
    "CPTS11": 1.5,
    "GGRC11": 1.5,
    "HGLG11": 1.5,
    "LVBI11": 1.5,
    "RBRY11": 1.5,
    "RECR11": 1.5,
    "RZTR11": 1.5,
    "VRTA11": 1.5,
    "SUZB3": 2.0,
    "BBDC4": 2.0,
    "CMIG4": 2.0,
    "SAPR11": 1.5,  # unit de saneamento, historicamente menos volátil
    "USDBRL": 1.0,
    "EURBRL": 1.0,
    "IEF": 1.0,
    "TLT": 1.5,
    "VWO": 1.5,
    "EWZ": 1.5,
}


def _vix_signal(price: Optional[float]) -> str:
    """Nota entre parênteses com a leitura do VIX ('Índice do Medo'), nas
    faixas pedidas pelo usuário — abaixo de 20 é ótimo, entre 20 e 30 pede
    atenção, acima de 30 é ruim. É sobre o nível absoluto do índice, não
    sobre a variação do dia (isso já é coberto pelo threshold em cima)."""
    if price is None:
        return ""
    if price < 20:
        return " (VIX - Índice do Medo: 🟢 ÓTIMO, abaixo de 20)"
    if price <= 30:
        return " (VIX - Índice do Medo: 🟡 ATENÇÃO, entre 20 e 30)"
    return " (VIX - Índice do Medo: 🔴 RUIM, acima de 30)"

# Portais aos quais o usuário pediu para restringir a busca de notícias
# (não inclui os comparadores de fundamentos como StatusInvest/Fundamentus/
# ClubeFII/etc. — esses não publicam "notícias" no sentido de RSS, são bases
# de indicadores; ver market_data.py / README para essa frente separada).
NEWS_DOMAINS = [
    "infomoney.com.br",
    "moneytimes.com.br",
    "br.investing.com",
    "einvestidor.estadao.com.br",
    "economia.uol.com.br",
    "valorinveste.globo.com",
    "valor.globo.com",
    "seudinheiro.com",
    "exame.com",
    "conteudos.xpi.com.br",
    "blog.toroinvestimentos.com.br",
    "blog.rico.com.vc",
    "empiricus.com.br",
    "genialinvestimentos.com.br",
    "nordresearch.com.br",
    "b3.com.br",
]
_NEWS_SITE_FILTER = "(" + " OR ".join(f"site:{d}" for d in NEWS_DOMAINS) + ")"

NEWS_QUERY = {
    "MGLU3": "MGLU3 Magazine Luiza ação",
    "LREN3": "LREN3 Lojas Renner ação",
    "PRIO3": "PRIO3 PetroRio ação",
    "BBAS3": "BBAS3 Banco do Brasil ação",
    "BRAV3": "BRAV3 Brava Energia ação",
    "PETR4": "PETR4 Petrobras ação",
    "BBSE3": "BBSE3 BB Seguridade ação",
    "ITSA4": "ITSA4 Itaúsa ação",
    "SUZB3": "SUZB3 Suzano Celulose ação",
    "BBDC4": "BBDC4 Bradesco ação",
    "CMIG4": "CMIG4 Cemig ação",
    "SAPR11": "SAPR11 Sanepar ação",
    "ALZR11": "ALZR11 FII Alianza Trust",
    "CPTS11": "CPTS11 FII Capitânia",
    "GGRC11": "GGRC11 FII GGR Covepi",
    "HGLG11": "HGLG11 FII CSHG Logística",
    "LVBI11": "LVBI11 FII VBI Logístico",
    "RBRY11": "RBRY11 FII Rio Bravo Renda",
    "RECR11": "RECR11 FII REC Recebíveis",
    "RZTR11": "RZTR11 FII Riza Terrax",
    "VRTA11": "VRTA11 FII Fator Verita",
}


# ---------------------------------------------------------------------------
# Estado (persistido em data/watch_state.json)
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    return {"date": None, "assets": {}, "seen_headlines": {}}


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return _default_state()


def save_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _reset_if_new_day(state: dict) -> dict:
    today = date.today().isoformat()
    if state.get("date") != today:
        state["date"] = today
        state["assets"] = {}
        state["snapshots_sent"] = []
        # mantém seen_headlines por 3 dias pra não perder contexto de virada
        # de dia, mas evita crescer pra sempre
        state.setdefault("seen_headlines", {})
    return state


# ---------------------------------------------------------------------------
# Notícias (Google News RSS — público, sem chave de API)
# ---------------------------------------------------------------------------

def fetch_headlines(query: str, when: str = "2d", limit: int = 8, restrict_domains: bool = True) -> list[str]:
    full_query = f"{query} {_NEWS_SITE_FILTER} when:{when}" if restrict_domains else f"{query} when:{when}"
    url = (
        "https://news.google.com/rss/search?q="
        + urlquote(full_query)
        + "&hl=pt-BR&gl=BR&ceid=BR:pt"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        titles = re.findall(r"<title>(.*?)</title>", resp.text)
        # as primeiras entradas são o título do feed/canal ("<query> - Google
        # Notícias", "Google Notícias"), não notícias de verdade.
        articles = [t for t in titles if t.strip() != "Google Notícias" and " - Google Notícias" not in t]
        return articles[:limit]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Modo intraday
# ---------------------------------------------------------------------------


# Horários (hora local, cheia) em que SEMPRE mandamos uma "foto" da variação
# do dia, independente de ter cruzado algum limiar — pedido explícito do
# usuário: abertura, meio do pregão e última hora do pregão B3.
SNAPSHOT_HOURS = {10: "abertura", 13: "meio do pregão", 16: "última hora"}


def _is_business_day(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5  # 0=segunda ... 4=sexta


def _in_trading_hours(now: Optional[datetime] = None) -> bool:
    """Guard usado pelo launchd (que dispara o intraday a cada 15 min,
    24/7, sem noção de horário de pregão) — o filtro de dia útil/horário
    fica no script, não no agendador, então funciona não importa quem
    chama (launchd, cron, tarefa do Claude, ou manual)."""
    now = now or datetime.now()
    return _is_business_day(now) and 10 <= now.hour <= 17


def build_snapshot_line(quotes: dict[str, "md.Quote"]) -> str:
    """Monta a linha de panorama: IBOV, S&P500 e os 5 ativos que mais
    variaram no momento, dentre tudo que está sendo monitorado."""
    parts = []
    ibov = quotes.get("IBOV")
    sp500 = quotes.get("SP500")
    if ibov and ibov.change_pct is not None:
        parts.append(f"IBOV {ibov.change_pct:+.2f}%")
    if sp500 and sp500.change_pct is not None:
        parts.append(f"S&P500 {sp500.change_pct:+.2f}%")

    movers = sorted(
        ((label, q) for label, q in quotes.items() if q.change_pct is not None and label not in ("IBOV", "SP500")),
        key=lambda kv: abs(kv[1].change_pct),
        reverse=True,
    )[:5]
    if movers:
        parts.append("maiores variações: " + ", ".join(f"{l} {q.change_pct:+.1f}%" for l, q in movers))
    return " | ".join(parts)


def run_intraday(dry_run: bool = False) -> int:
    if not _in_trading_hours():
        print("Fora do horário de pregão (10h-17h, dias úteis) — nada a fazer.")
        return 0

    state = _reset_if_new_day(load_state())
    triggers: list[str] = []

    # 1) limiares de preço — busca tudo de uma vez (1 requisição por
    # screener) em vez de uma chamada por ativo, pra não tomar 429 do
    # TradingView com 40+ ativos monitorados.
    quotes_cache = md.get_quotes_batch(list(THRESHOLDS.keys()))
    for label, threshold in THRESHOLDS.items():
        quote = quotes_cache[label]
        if quote.error or quote.change_pct is None:
            continue
        change = quote.change_pct
        entry = state["assets"].get(label, {})
        already_alerted = entry.get("alerted", False)
        last_alert_change = entry.get("last_alert_change")

        crossed = abs(change) >= threshold
        moved_further = (
            last_alert_change is not None and abs(change - last_alert_change) >= threshold
        )
        should_alert = crossed and (not already_alerted or moved_further)

        if should_alert:
            arrow = "📈" if change >= 0 else "📉"
            extra = _vix_signal(quote.price) if label == "VIX" else ""
            if label in md.BRL_ASSETS:
                triggers.append(f"{arrow} {label} {change:+.1f}% (R${quote.price}){extra}")
            else:
                triggers.append(f"{arrow} {label} {change:+.1f}% ({quote.price}){extra}")
            state["assets"][label] = {"alerted": True, "last_alert_change": change}
        else:
            state["assets"].setdefault(label, {"alerted": already_alerted, "last_alert_change": last_alert_change})

    # 2) projeção de juros (Boletim Focus/BCB, proxy do DI) mudou desde a
    # última checagem? Isso atualiza semanalmente, então na prática só
    # dispara quando o BC publica um novo boletim (não gera spam horário).
    focus = md.get_focus_projections(years=1)
    if not focus.get("error") and focus.get("projecoes"):
        p = focus["projecoes"][0]
        current = p.get("selic_hoje")
        last = state.get("focus_selic")
        if current is not None and last is not None and current != last:
            arrow = "📈" if current > last else "📉"
            triggers.append(f"{arrow} Projeção Selic {p['ano']} (Focus): {last}% → {current}%")
        if current is not None:
            state["focus_selic"] = current

    # 3) manchetes novas para as ações e FIIs monitorados
    seen = state.setdefault("seen_headlines", {})
    for ticker, query in NEWS_QUERY.items():
        headlines = fetch_headlines(query)
        seen_list = seen.get(ticker, [])
        new_ones = [h for h in headlines if h not in seen_list]
        if new_ones:
            # só reporta a manchete mais recente pra não lotar o push
            triggers.append(f"📰 {ticker}: {new_ones[0][:100]}")
        seen[ticker] = (new_ones + seen_list)[:30]

    # Checagem agora roda a cada 15 min (era de hora em hora) pra avisar
    # ASAP — mas o panorama fixo (abertura/meio/última hora) deve sair só
    # UMA vez em cada janela, não a cada 15 min dentro dela.
    hour = datetime.now().hour
    sent_today = state.setdefault("snapshots_sent", [])
    snapshot_label = SNAPSHOT_HOURS.get(hour) if hour not in sent_today else None
    if snapshot_label:
        sent_today.append(hour)

    save_state(state)

    if not triggers and not snapshot_label:
        print("Sem gatilhos relevantes nesta checagem.")
        return 0

    parts = []
    if snapshot_label:
        snapshot_line = build_snapshot_line(quotes_cache)
        title = _bold(f"📊 Panorama B3 — {snapshot_label} ({hour}h)")
        parts.append(f"{title}\n{snapshot_line}")
    if triggers:
        title = _bold("🔔 Alertas de variação/notícia:")
        parts.append(f"{title}\n" + "\n".join(triggers[:6]))

    message = "\n\n".join(parts)
    print(message)
    if not dry_run:
        # parse_mode=None: o título já vem em negrito Unicode (_bold), e as
        # manchetes de notícia são texto dinâmico — um "_"/"*" desencontrado
        # nelas derrubaria a mensagem inteira se usasse Markdown de verdade.
        results = _send_to_all_channels(message, parse_mode=None)
        erros = _channel_errors(results)
        if erros:
            print(f"AVISO: falha ao enviar — {'; '.join(erros)}", file=sys.stderr)
            return 1
    return 0


# ---------------------------------------------------------------------------
# Modo daily (resumo de fechamento)
# ---------------------------------------------------------------------------

# Regiões que o usuário pediu pra resumir de forma qualitativa (sem listar
# cada índice/número) em vez do detalhamento ativo a ativo.
QUALITATIVE_CATEGORIES = {"EUROPA", "ÁSIA", "AMÉRICA LATINA"}


def _qualitative_read(quotes: dict, items: list[tuple[str, str]]) -> str:
    """Consolida vários índices de uma região numa leitura só, sem expor o
    número de cada um — só a direção/intensidade média e se o movimento é
    generalizado (todos na mesma direção) ou misto."""
    changes = [quotes[l].change_pct for l, _ in items if quotes.get(l) and quotes[l].change_pct is not None]
    if not changes:
        return "indisponível"

    avg = sum(changes) / len(changes)
    if avg >= 1.5:
        label = "forte alta"
    elif avg >= 0.3:
        label = "alta moderada"
    elif avg > -0.3:
        label = "estável, sem direção clara"
    elif avg > -1.5:
        label = "queda moderada"
    else:
        label = "forte queda"

    positivos = sum(1 for c in changes if c > 0)
    negativos = sum(1 for c in changes if c < 0)
    if positivos == len(changes) or negativos == len(changes):
        consenso = "movimento generalizado (todas as praças na mesma direção)"
    else:
        consenso = "mercado misto (nem todas as praças foram na mesma direção)"

    arrow = "🟢" if avg >= 0.3 else ("🔴" if avg <= -0.3 else "🟡")
    return f"{arrow} {label} — {consenso}"


def build_global_panorama() -> list[str]:
    """Seção de fechamento estilo Google Finance: EUA, Europa, Ásia, América
    Latina, Moedas, Futuros — sem cripto (não pedido). Europa/Ásia/América
    Latina saem de forma qualitativa (consolidada), a pedido do usuário —
    sem listar índice por índice."""
    all_labels = [label for items in md.GLOBAL_MARKET_CATEGORIES.values() for label, _ in items]
    quotes = md.get_quotes_batch(all_labels)

    lines = ["## 🌎 Panorama global de fechamento"]
    for category, items in md.GLOBAL_MARKET_CATEGORIES.items():
        lines.append(f"**{category}**")
        if category in QUALITATIVE_CATEGORIES:
            lines.append(f"- {_qualitative_read(quotes, items)}")
            continue
        ranked_items = sorted(
            items,
            key=lambda it: (quotes.get(it[0]) is None or quotes[it[0]].change_pct is None, -(quotes[it[0]].change_pct or 0) if quotes.get(it[0]) else 0),
        )
        for label, nome in ranked_items:
            q = quotes.get(label)
            if q is None or q.error or q.change_pct is None:
                lines.append(f"- {nome}: indisponível")
            else:
                arrow = "🟢" if q.change_pct >= 0 else "🔴"
                lines.append(f"- {arrow} {nome}: {q.price} ({q.change_pct:+.2f}%)")
    lines.append("")
    return lines


def _put_call_bias(ratio: Optional[float]) -> tuple[str, str]:
    """Classifica o put/call ratio (por volume) numa leitura qualitativa.
    Não é recomendação — só descreve o que o volume de opções do dia mostra.
    "Forte" ganha 3 emojis (pedido do usuário) pra saltar aos olhos mais
    que o viés "normal", que fica com 1."""
    if ratio is None:
        return "❔", "sem negócios suficientes"
    if ratio >= 3.0:
        return "🐻🐻🐻", "forte viés de puts"
    if ratio >= 1.3:
        return "🐻", "viés de puts"
    if ratio > 0.7:
        return "⚖️", "equilibrado"
    if ratio > 0.3:
        return "🐂", "viés de calls"
    return "🐂🐂🐂", "forte viés de calls"


def _fmt_contrato(c: dict) -> str:
    """Formata um contrato (do 'top_mais_negociados' ou 'maior_contrato_por_prazo')
    numa linha curta: tipo, strike, vencimento aproximado e volume. O prazo
    (curto/médio/longo) já vem no rótulo de quem chama, não repete aqui."""
    venc = f" ({c['vencimento_aprox']})" if c.get("vencimento_aprox") else ""
    return f"{c['tipo']} R${c['strike']:.2f}{venc} — R${c['volume_financeiro']:,.0f}"


def _greeks_alert(tipo: str, greeks: Optional[dict]) -> Optional[str]:
    """Mini comentário descritivo (não é recomendação) sobre o que o
    Delta/Gama ESTIMADOS do contrato mais líquido implicam pra quem já
    tem ou está olhando esse contrato — sensibilidade, não sugestão."""
    if not greeks:
        return None
    delta = greeks.get("delta_estimado")
    if delta is None:
        return None

    if not greeks.get("confiavel", True):
        motivo = greeks.get("motivo", "")
        direcao = "sobe" if delta > 0 else "cai" if delta < 0 else "não reage"
        return (
            f"⚠️ Δ≈{delta:+.2f} (estimativa não confiável — {motivo}). "
            f"Na prática, um contrato assim tende a andar quase junto com a ação: se a ação {direcao}, o contrato tende a acompanhar de perto."
        )

    iv = greeks.get("iv_estimada_pct")
    gamma = greeks.get("gamma_estimado") or 0
    abs_delta = abs(delta)

    if abs_delta >= 0.7:
        sensibilidade = "alta sensibilidade ao preço da ação (se move quase junto)"
    elif abs_delta >= 0.35:
        sensibilidade = "sensibilidade moderada (acompanha parte do movimento da ação)"
    else:
        sensibilidade = "baixa sensibilidade (precisa a ação andar bastante pra sentir efeito)"

    alerta_gama = ""
    if gamma >= 0.15:
        alerta_gama = " ⚠️ Gama alto: pouca variação no preço da ação pode mudar bastante o valor do contrato rapidamente (comum perto do vencimento)."

    direcao = "ganha valor se a ação SOBE" if (tipo == "CALL") else "ganha valor se a ação CAI"
    return f"Δ{delta:+.2f} Γ{gamma:.3f} IV~{iv}% — {direcao}, {sensibilidade}.{alerta_gama}"


def build_options_highlight(all_options: dict, watchlist: dict) -> list[str]:
    """Seção EM DESTAQUE, pedida explicitamente pelo usuário: put/call ratio
    de cada ação (só ações — FIIs não têm opções), ordenado do viés mais
    defensivo (mais puts) ao mais otimista (mais calls), com preço do dia,
    ratio separado por dentro/fora do dinheiro (ITM/OTM) e o maior contrato
    por prazo (curto/médio/longo) — tudo leitura de fluxo do dia, não é
    recomendação de compra/venda."""
    lines = ["## 🎯🎯 DESTAQUE — Viés de opções por ação (put/call ratio)", ""]
    rows = []
    for ticker in md.OPTIONS_TICKERS:
        opt = all_options.get(ticker, {})
        ratio = opt.get("put_call_ratio_volume")
        rows.append((ticker, ratio, opt))

    # ordena do maior ratio (mais puts) pro menor (mais calls); None por
    # último (sem negócio suficiente pra calcular)
    rows.sort(key=lambda r: (r[1] is None, -(r[1] or 0)))

    for ticker, ratio, opt in rows:
        emoji, texto = _put_call_bias(ratio)
        q = watchlist.get(ticker, {})
        preco_txt = f"R${q['price']} ({q['change_pct']:+.1f}%)" if q.get("price") is not None else "preço indisponível"
        if opt.get("error"):
            lines.append(f"- {emoji} **{ticker}**: sem dados de opções hoje ({preco_txt})")
            continue
        ratio_txt = f"{ratio:.2f}" if ratio is not None else "n/d"
        lines.append(
            f"- {emoji} **{ticker}**: put/call {ratio_txt} — {texto} "
            f"| {opt.get('contratos_negociados_hoje', 0)} contratos | {preco_txt}"
        )

        # linha 2: ratio dentro (ITM) vs fora (OTM) do dinheiro — mostra se
        # o viés vem de gente ajustando posição já "no dinheiro" (ITM) ou
        # de aposta/hedge mais especulativo, fora do dinheiro (OTM).
        itm = opt.get("put_call_ratio_itm")
        otm = opt.get("put_call_ratio_otm")
        itm_txt = f"{itm:.2f}" if itm is not None else "n/d"
        otm_txt = f"{otm:.2f}" if otm is not None else "n/d"
        lines.append(f"  ↳ put/call dentro do dinheiro (ITM): {itm_txt} | fora do dinheiro (OTM): {otm_txt}")

        # linha 3: maior contrato absoluto no curto e no médio prazo (se
        # houver negócio nesses baldes — nem toda ação tem os dois).
        por_prazo = opt.get("maior_contrato_por_prazo") or {}
        destaques_prazo = [
            f"{prazo}: {_fmt_contrato(c)}"
            for prazo, c in por_prazo.items()
            if prazo in ("curto prazo", "médio prazo")
        ]
        if destaques_prazo:
            lines.append("  ↳ maior contrato — " + " | ".join(destaques_prazo))

        # linha 4: Delta/Gama ESTIMADOS (Black-Scholes, ver aviso no rodapé)
        # do contrato mais líquido — + mini comentário de alta/queda.
        top = (opt.get("top_mais_negociados") or [None])[0]
        if top:
            alerta = _greeks_alert(top["tipo"], top.get("greeks_estimados"))
            if alerta:
                lines.append(f"  ↳ Δ/Γ estimados ({top['tipo']} R${top['strike']:.2f}): {alerta}")
    lines.append("")
    lines.append(
        "_Ratio alto = mais volume em puts que calls no dia (pode ser hedge/proteção ou aposta em queda); "
        "baixo = mais volume em calls (pode ser hedge de venda coberta ou aposta em alta). "
        "Leitura de fluxo do dia, não é recomendação de compra/venda. "
        "Delta/Gama são ESTIMADOS por mim via Black-Scholes (opcoes.net.br bloqueia o valor real sem login) — "
        "não são o número exato do book, especialmente perto do vencimento em contratos bem dentro/fora do dinheiro._"
    )
    lines.append("")
    return lines


def _fmt_br_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def build_focus_section() -> list[str]:
    """Projeção de juros (Boletim Focus) + calendário do Copom: data da
    próxima reunião e variação da Selic (Focus) desde a última reunião.

    A comparação "antes x depois" exata do Copom depende de termos
    guardado o valor ANTES da reunião — como só passamos a monitorar
    agora, isso vai ficando cada vez mais preciso a partir da próxima
    reunião em diante (o valor de hoje já fica salvo como referência).
    """
    lines = ["## 🎯 Projeção de juros — Boletim Focus/BCB (proxy do DI, ~140 analistas)"]
    focus = md.get_focus_projections()
    if focus.get("error") or not focus.get("projecoes"):
        lines.append(f"- indisponível ({focus.get('error', 'sem dados')})")
        lines.append("")
        return lines

    projecoes = focus["projecoes"]
    ano_atual = projecoes[0]
    calendario = md.get_copom_calendar()

    # guarda o valor de hoje no histórico (usado pra calcular a variação
    # "desde a última reunião" nas próximas execuções)
    state = load_state()
    history = state.setdefault("focus_selic_history", {})
    today_str = date.today().isoformat()
    if ano_atual.get("selic_hoje") is not None:
        history[today_str] = ano_atual["selic_hoje"]
        cutoff = (date.today() - timedelta(days=70)).isoformat()
        for d in list(history.keys()):
            if d < cutoff:
                del history[d]
    save_state(state)

    proxima = calendario.get("proxima_reuniao")
    dias = calendario.get("dias_ate_proxima")
    ultima = calendario.get("ultima_reuniao")

    if proxima:
        plural = "s" if dias != 1 else ""
        lines.append(f"📅 Próxima reunião do Copom: **{_fmt_br_date(proxima)}** (em {dias} dia{plural})")

    if ultima and ano_atual.get("selic_hoje") is not None:
        antes = {d: v for d, v in history.items() if d < ultima}
        if antes:
            data_ref = max(antes.keys())
            valor_antes = antes[data_ref]
            atual = ano_atual["selic_hoje"]
            delta = round(atual - valor_antes, 3)
            seta = "⬆️" if delta > 0 else ("⬇️" if delta < 0 else "➡️")
            lines.append(
                f"{seta} Selic {ano_atual['ano']} (Focus) desde a reunião de {_fmt_br_date(ultima)}: "
                f"{valor_antes}% → {atual}% ({delta:+.2f} p.p.)"
            )
        else:
            lines.append(
                f"ℹ️ Ainda sem valor registrado de antes da reunião de {_fmt_br_date(ultima)} "
                f"(começamos a monitorar depois dela) — a partir da próxima reunião essa comparação fica exata."
            )

    if ano_atual.get("selic_ha_4_semanas") is not None and ano_atual.get("selic_hoje") is not None:
        h4, atual = ano_atual["selic_ha_4_semanas"], ano_atual["selic_hoje"]
        delta4 = round(atual - h4, 3)
        lines.append(
            f"_(referência adicional: há 4 semanas a Selic {ano_atual['ano']} projetada era {h4}%, hoje {atual}% "
            f"({delta4:+.2f} p.p.) — não é exatamente 'desde o Copom', que se reúne a cada ~45 dias, não 28)_"
        )

    lines.append("")
    for p in projecoes:
        lines.append(
            f"- **{p['ano']}**: Selic {p.get('selic_hoje')}% | IPCA {p.get('ipca_hoje')}% | Câmbio R$ {p.get('cambio_hoje')} | PIB {p.get('pib_hoje')}%"
        )
    lines.append(
        "_Não é o preço do futuro de DI1 (sem fonte gratuita) — é a mediana das expectativas dos analistas "
        "consultados pelo BC, atualizada semanalmente._"
    )
    lines.append("")
    return lines


def build_daily_report() -> str:
    lines = [f"# Resumo B3 — {datetime.now().strftime('%d/%m/%Y %H:%M')}", ""]

    lines.extend(build_global_panorama())

    watchlist = md.get_watchlist()
    all_options = {
        ticker: md.get_options_summary(ticker, top_n=3, spot_price=watchlist.get(ticker, {}).get("price"))
        for ticker in md.OPTIONS_TICKERS
    }

    lines.extend(build_options_highlight(all_options, watchlist))

    lines.append("## Preços B3 (variação vs fechamento anterior) — do maior pro menor")
    ranked = sorted(
        watchlist.items(),
        key=lambda kv: (kv[1].get("change_pct") is None, -(kv[1].get("change_pct") or 0)),
    )
    for label, q in ranked:
        if q.get("error"):
            lines.append(f"- {label}: indisponível ({q['error']})")
        else:
            extra = _vix_signal(q["price"]) if label == "VIX" else ""
            lines.append(f"- **{label}**: {q['price']} ({q['change_pct']:+.2f}%){extra}")
    lines.append("")

    lines.extend(build_focus_section())

    lines.append("## Fluxo de investidor estrangeiro (B3, últimos dias)")
    fluxo = md.get_foreign_flow(days=5)
    if fluxo.get("error"):
        lines.append(f"- indisponível ({fluxo['error']})")
    else:
        for row in fluxo["rows"]:
            lines.append(f"- {row['data']}: estrangeiro {row['estrangeiro_mi']:+.1f} mi (institucional {row['institucional_mi']:+.1f} mi, pessoa física {row['pessoa_fisica_mi']:+.1f} mi)")
    lines.append("")

    lines.append("## Opções — detalhe por ação (opcoes.net.br, sem recomendação)")
    for ticker in md.OPTIONS_TICKERS:
        opt = all_options[ticker]
        if opt.get("error"):
            lines.append(f"### {ticker}\n- indisponível ({opt['error']})")
            continue
        lines.append(f"### {ticker}")
        lines.append(
            f"- {opt['contratos_negociados_hoje']} contratos negociados | "
            f"put/call ratio (volume): {opt['put_call_ratio_volume']}"
        )
        for t in opt["top_mais_negociados"]:
            lines.append(
                f"  - {t['tipo']} strike {t['strike']} ({t['moneyness']}): "
                f"{t['num_negocios']} negócios, R$ {t['volume_financeiro']:,.0f}"
            )
    lines.append("")
    lines.append(
        "_Isto é um resumo informativo, não é recomendação de investimento. "
        "Volatilidade implícita e posição em aberto não disponíveis sem login em opcoes.net.br._"
    )
    return "\n".join(lines)


def run_daily(dry_run: bool = False) -> int:
    if not _is_business_day():
        print("Fim de semana — sem resumo (a B3 não abre).")
        return 0

    report = build_daily_report()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LAST_SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    watchlist = md.get_watchlist()
    fluxo = md.get_foreign_flow(days=1)
    flow_txt = ""
    if not fluxo.get("error") and fluxo.get("rows"):
        v = fluxo["rows"][0]["estrangeiro_mi"]
        flow_txt = f" | Fluxo estrangeiro: {v:+.0f}mi"

    focus_txt = ""
    focus = md.get_focus_projections(years=1)
    if not focus.get("error") and focus.get("projecoes"):
        p = focus["projecoes"][0]
        focus_txt = f" | Selic {p['ano']} (Focus): {p.get('selic_hoje')}%"

    highlights = []
    for label in ("IBOV", "PRIO3", "MGLU3", "BBAS3", "LREN3", "BRAV3", "PETR4", "BBSE3", "ITSA4"):
        q = watchlist.get(label, {})
        if q.get("price") is not None:
            highlights.append(f"{label} {q['change_pct']:+.1f}%")

    message = "📊 Resumo diário B3: " + " | ".join(highlights) + flow_txt + focus_txt
    print(message)
    print()
    print(report)

    if dry_run:
        return 0

    to_send = [message] + _chunk_for_telegram(report)
    for i, chunk in enumerate(to_send):
        # texto puro pro relatório longo: ele tem **negrito**/_itálico_ estilo
        # Markdown "normal", que não é o dialeto que a API legada do Telegram
        # aceita (só *um asterisco*) — um marcador desencontrado derruba a
        # mensagem inteira. Mais seguro não tentar formatar. (Discord manda
        # como texto puro sempre, sem parse_mode — não precisa desse cuidado.)
        results = _send_to_all_channels(chunk, parse_mode=None)
        erros = _channel_errors(results)
        if erros:
            print(f"AVISO: falha ao enviar (parte {i+1}/{len(to_send)}) — {'; '.join(erros)}", file=sys.stderr)
            return 1
    return 0


def _chunk_for_telegram(text: str, max_len: int = 3800) -> list[str]:
    """Quebra um texto longo em pedaços <= max_len (limite real do Telegram é
    4096; deixamos folga). Prefere cortar em fronteiras de seção ("## "),
    mas se uma seção sozinha for maior que max_len (ex.: opções de 12
    ações), quebra por linha dentro dela — nunca deixa um pedaço estourar."""
    sections = re.split(r"(?=^## )", text, flags=re.M)
    chunks: list[str] = []
    current = ""

    def _flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for section in sections:
        if len(section) > max_len:
            # seção grande demais até pra virar 1 chunk sozinha: quebra linha
            # a linha, sem perder o texto.
            for line in section.split("\n"):
                if len(current) + len(line) + 1 > max_len:
                    _flush()
                current += line + "\n"
            continue
        if len(current) + len(section) > max_len:
            _flush()
        current += section
    _flush()
    return chunks


# ---------------------------------------------------------------------------
# Modo screen (relatório completo sob demanda, sem Telegram)
# ---------------------------------------------------------------------------

def run_screen() -> int:
    print(build_daily_report())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["intraday", "daily", "screen"], required=True)
    parser.add_argument("--dry-run", action="store_true", help="não envia Telegram, só imprime")
    args = parser.parse_args()

    if args.mode == "intraday":
        return run_intraday(dry_run=args.dry_run)
    if args.mode == "daily":
        return run_daily(dry_run=args.dry_run)
    return run_screen()


if __name__ == "__main__":
    raise SystemExit(main())
