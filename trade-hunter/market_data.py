"""
Trade Hunter — dados de mercado ampliados (ações + índices + fluxo estrangeiro
+ leitura objetiva de opções).

Este módulo NÃO depende do pacote `mcp` (que exige Python 3.10+); usa apenas
`requests` / `beautifulsoup4`, iguais ao resto do projeto, para poder rodar
com o Python do sistema (3.9) via scripts (watch.py) e scheduled tasks.

IMPORTANTE (compliance): nada aqui gera recomendação de compra/venda. As
funções só organizam dados objetivos (preço, variação, fluxo, volume de
opções) para o usuário decidir por conta própria.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

from sources import Quote, get_tradingview_quote, DEFAULT_HEADERS  # reaproveita o que já existe

# ---------------------------------------------------------------------------
# Mapa de ativos monitorados (ações B3 + índices/referências via TradingView)
# ---------------------------------------------------------------------------
# Cada entrada: label amigável -> (símbolo TradingView, screener do scanner)
ASSET_MAP: dict[str, tuple[str, str]] = {
    "MGLU3": ("BMFBOVESPA:MGLU3", "brazil"),
    "LREN3": ("BMFBOVESPA:LREN3", "brazil"),
    "PRIO3": ("BMFBOVESPA:PRIO3", "brazil"),
    "BBAS3": ("BMFBOVESPA:BBAS3", "brazil"),
    "BRAV3": ("BMFBOVESPA:BRAV3", "brazil"),
    "PETR4": ("BMFBOVESPA:PETR4", "brazil"),
    "BBSE3": ("BMFBOVESPA:BBSE3", "brazil"),
    "ITSA4": ("BMFBOVESPA:ITSA4", "brazil"),
    "SUZB3": ("BMFBOVESPA:SUZB3", "brazil"),
    "BBDC4": ("BMFBOVESPA:BBDC4", "brazil"),
    "CMIG4": ("BMFBOVESPA:CMIG4", "brazil"),
    "SAPR11": ("BMFBOVESPA:SAPR11", "brazil"),
    # FIIs selecionados pelo usuário — sem opções listadas na B3 (confirmado:
    # opcoes.net.br não retorna contratos para FIIs), por isso ficam de fora
    # de OPTIONS_TICKERS.
    "ALZR11": ("BMFBOVESPA:ALZR11", "brazil"),
    "CPTS11": ("BMFBOVESPA:CPTS11", "brazil"),
    "GGRC11": ("BMFBOVESPA:GGRC11", "brazil"),
    "HGLG11": ("BMFBOVESPA:HGLG11", "brazil"),
    "LVBI11": ("BMFBOVESPA:LVBI11", "brazil"),
    "RBRY11": ("BMFBOVESPA:RBRY11", "brazil"),
    "RECR11": ("BMFBOVESPA:RECR11", "brazil"),
    "RZTR11": ("BMFBOVESPA:RZTR11", "brazil"),
    "VRTA11": ("BMFBOVESPA:VRTA11", "brazil"),
    "IBOV": ("BMFBOVESPA:IBOV", "brazil"),
    # Pedido era IBrX-100 (IBXX), mas esse código não existe no scanner
    # gratuito do TradingView — IBXL (IBrX-50) foi o parente mais próximo
    # que resolveu. Ver README para detalhes.
    "IBXL": ("BMFBOVESPA:IBXL", "brazil"),
    "VIX": ("CBOE:VIX", "america"),
    "BRENT": ("NYMEX:BZ1!", "futures"),
    "SPY": ("AMEX:SPY", "america"),  # ETF que replica o S&P 500 — o "índice que importa" da bolsa americana (pedido do usuário, substitui o SP500 bruto)
    "USDBRL": ("FX_IDC:USDBRL", "forex"),
    "EURBRL": ("FX_IDC:EURBRL", "forex"),
    "IEF": ("NASDAQ:IEF", "america"),   # ETF títulos do Tesouro americano 7-10 anos
    "TLT": ("NASDAQ:TLT", "america"),   # ETF títulos do Tesouro americano 20+ anos
    "VWO": ("AMEX:VWO", "america"),     # ETF ações de mercados emergentes
    "EWZ": ("AMEX:EWZ", "america"),     # ETF ações brasileiras negociado nos EUA (iShares MSCI Brazil)

    # ---- Panorama global de fechamento (resumo diário, estilo Google Finance) ----
    # EUA
    "DOW": ("DJ:DJI", "america"),
    "NASDAQ100": ("NASDAQ:NDX", "america"),
    # Ásia
    "NIKKEI225": ("TVC:NI225", "cfd"),
    "SHANGHAI": ("SSE:000001", "china"),
    "KOSPI": ("TVC:KOSPI", "cfd"),
    # Moedas (além de USDBRL/EURBRL já mapeados acima)
    "DXY": ("TVC:DXY", "cfd"),
    # Futuros
    "YM_FUT": ("CBOT_MINI:YM1!", "futures"),  # E-mini Dow
    "NQ_FUT": ("CME_MINI:NQ1!", "futures"),   # E-mini Nasdaq
    "GOLD_FUT": ("COMEX:GC1!", "futures"),
    "WTI_FUT": ("NYMEX:CL1!", "futures"),
    # DI1 (juros futuros B3) não tem fonte gratuita confiável em tempo real
    # via scanner do TradingView nem via dadosdemercado sem conta paga — fica
    # de fora do preço automático; ver README para o porquê.
}

# Panorama global de fechamento (resumo diário) — categorias no estilo
# Google Finance, sem cripto (não pedido). Cada item: (label do ASSET_MAP,
# nome amigável para exibição).
GLOBAL_MARKET_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    # Europa e os índices individuais abaixo (Merval, Mexbol, FTSE100,
    # Hang Seng, ES_FUT, SP500 bruto, Euro Stoxx 50, DAX, CAC40, Nasdaq
    # Composto) foram removidos a pedido do usuário — ou redundantes com
    # outro índice já monitorado, ou sem utilidade pra decisão do dia a dia.
    "EUA": [
        ("DOW", "Dow Jones"),
        ("SPY", "S&P 500 (via SPY)"),
        ("NASDAQ100", "Nasdaq 100"),
    ],
    "ÁSIA": [
        ("NIKKEI225", "Nikkei 225 (Japão)"),
        ("SHANGHAI", "Xangai Composto"),
        ("KOSPI", "Kospi (Coreia do Sul)"),
    ],
    "AMÉRICA LATINA": [
        ("IBOV", "Ibovespa"),
        ("EWZ", "EWZ (ETF Brasil nos EUA)"),
    ],
    "MOEDAS": [
        ("USDBRL", "Dólar/Real"),
        ("EURBRL", "Euro/Real"),
        ("DXY", "Índice do Dólar (DXY)"),
    ],
    "FUTUROS": [
        ("YM_FUT", "E-mini Dow"),
        ("NQ_FUT", "E-mini Nasdaq"),
        ("GOLD_FUT", "Ouro"),
        ("WTI_FUT", "Petróleo WTI"),
        ("BRENT", "Petróleo Brent"),
    ],
}

# Ações cujas opções são acompanhadas em opcoes.net.br
OPTIONS_TICKERS = ["MGLU3", "LREN3", "PRIO3", "BBAS3", "BRAV3", "PETR4", "BBSE3", "ITSA4", "SUZB3", "BBDC4", "CMIG4", "SAPR11"]

# FIIs selecionados (sem opções na B3 — ver comentário acima em ASSET_MAP)
FII_TICKERS = ["ALZR11", "CPTS11", "GGRC11", "HGLG11", "LVBI11", "RBRY11", "RECR11", "RZTR11", "VRTA11"]

# Ativos cotados em R$ na B3 (ações + FIIs) — usado só pra formatar mensagens
# (o resto — índices, câmbio, ETF americano — não é "R$").
BRL_ASSETS = OPTIONS_TICKERS + FII_TICKERS

TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/{screener}/scan"


def get_quote_by_label(label: str) -> Quote:
    """Busca a cotação (TradingView) de um ativo do ASSET_MAP pelo label
    amigável (ex.: "IBOV", "VIX", "BRENT", "SPY", "PRIO3")."""
    if label not in ASSET_MAP:
        return Quote(source="tradingview", symbol=label, price=None, error=f"Ativo '{label}' não mapeado.")
    tv_symbol, screener = ASSET_MAP[label]
    return _scan_one(tv_symbol, screener)


def _scan_one(tv_symbol: str, screener: str, timeout: int = 10) -> Quote:
    url = TRADINGVIEW_SCAN_URL.format(screener=screener)
    payload = {
        "symbols": {"tickers": [tv_symbol], "query": {"types": []}},
        "columns": ["close", "change", "open", "high", "low", "volume"],
    }
    try:
        resp = requests.post(url, json=payload, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = (resp.json().get("data") or [])
        if not data:
            return Quote(source="tradingview", symbol=tv_symbol, price=None, error="símbolo não encontrado")
        row = dict(zip(["close", "change", "open", "high", "low", "volume"], data[0].get("d") or []))
        return Quote(
            source="tradingview",
            symbol=tv_symbol,
            price=row.get("close"),
            change_pct=row.get("change"),
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            volume=row.get("volume"),
        )
    except Exception as exc:  # noqa: BLE001
        return Quote(source="tradingview", symbol=tv_symbol, price=None, error=str(exc))


def get_quotes_batch(labels: list[str], timeout: int = 15) -> dict[str, Quote]:
    """Busca vários labels de uma vez, agrupando por screener e mandando UMA
    requisição por screener (o endpoint do scanner aceita uma lista de
    tickers). Crítico pra não tomar 429 (Too Many Requests) do TradingView —
    com 40+ ativos monitorados, buscar um por um em sequência estoura o
    limite de chamadas rapidinho."""
    by_screener: dict[str, list[tuple[str, str]]] = {}
    for label in labels:
        if label not in ASSET_MAP:
            continue
        tv_symbol, screener = ASSET_MAP[label]
        by_screener.setdefault(screener, []).append((label, tv_symbol))

    results: dict[str, Quote] = {}
    for screener, items in by_screener.items():
        symbols = [tv for _, tv in items]
        url = TRADINGVIEW_SCAN_URL.format(screener=screener)
        payload = {
            "symbols": {"tickers": symbols, "query": {"types": []}},
            "columns": ["close", "change", "open", "high", "low", "volume"],
        }
        try:
            resp = requests.post(url, json=payload, headers=DEFAULT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            data = resp.json().get("data") or []
            by_symbol = {d["s"]: (d.get("d") or []) for d in data}
        except Exception as exc:  # noqa: BLE001
            for label, tv_symbol in items:
                results[label] = Quote(source="tradingview", symbol=tv_symbol, price=None, error=str(exc))
            continue

        for label, tv_symbol in items:
            values = by_symbol.get(tv_symbol)
            if values is None:
                results[label] = Quote(source="tradingview", symbol=tv_symbol, price=None, error="símbolo não encontrado")
                continue
            row = dict(zip(["close", "change", "open", "high", "low", "volume"], values))
            results[label] = Quote(
                source="tradingview",
                symbol=tv_symbol,
                price=row.get("close"),
                change_pct=row.get("change"),
                open=row.get("open"),
                high=row.get("high"),
                low=row.get("low"),
                volume=row.get("volume"),
            )
    return results


def get_watchlist() -> dict[str, dict]:
    """Retorna cotação atual de todos os ativos do ASSET_MAP (ações + índices)."""
    quotes = get_quotes_batch(list(ASSET_MAP.keys()))
    return {label: quotes[label].to_dict() for label in ASSET_MAP}


# ---------------------------------------------------------------------------
# Fluxo de investidor estrangeiro (dadosdemercado.com.br — página pública,
# sem necessidade de login/API paga; dado divulgado com defasagem de 1-2
# pregões, igual ao praticado pela própria B3).
# ---------------------------------------------------------------------------

FLUXO_URL = "https://www.dadosdemercado.com.br/fluxo"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


@dataclass
class FluxoDia:
    data: str
    estrangeiro_mi: Optional[float]
    institucional_mi: Optional[float]
    pessoa_fisica_mi: Optional[float]
    inst_financeira_mi: Optional[float]
    outros_mi: Optional[float]


def _parse_mi(text: str) -> Optional[float]:
    """Converte string tipo '1.393,22 mi' ou '-86,20 mi' em float (milhões de R$)."""
    if not text:
        return None
    t = text.strip().replace("mi", "").strip()
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def get_foreign_flow(days: int = 10) -> dict:
    """Faz scraping da tabela pública de fluxo de investidores da B3
    (dadosdemercado.com.br/fluxo). Retorna as últimas `days` linhas.

    Nunca lança exceção — em caso de falha, devolve {"error": "..."}.
    """
    try:
        resp = requests.get(FLUXO_URL, headers=BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao buscar {FLUXO_URL}: {exc}"}

    # A tabela renderiza linhas "DD/MM/AAAA | Estrangeiro | Institucional |
    # Pessoa física | Inst. Financeira | Outros", uma data e 5 valores em
    # "mi" por linha, na ordem em que aparecem no HTML renderizado como texto.
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S)
    text = re.sub("<[^>]+>", "\n", text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    date_re = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    value_re = re.compile(r"^-?[\d.,]+ mi$")

    rows: list[FluxoDia] = []
    i = 0
    while i < len(lines) and len(rows) < days:
        if date_re.match(lines[i]):
            vals = []
            j = i + 1
            while j < len(lines) and len(vals) < 5 and value_re.match(lines[j]):
                vals.append(_parse_mi(lines[j]))
                j += 1
            if len(vals) == 5:
                rows.append(
                    FluxoDia(
                        data=lines[i],
                        estrangeiro_mi=vals[0],
                        institucional_mi=vals[1],
                        pessoa_fisica_mi=vals[2],
                        inst_financeira_mi=vals[3],
                        outros_mi=vals[4],
                    )
                )
                i = j
                continue
        i += 1

    if not rows:
        return {"error": "não consegui extrair a tabela (layout da página pode ter mudado)", "source_url": FLUXO_URL}

    return {"source_url": FLUXO_URL, "rows": [r.__dict__ for r in rows]}


# ---------------------------------------------------------------------------
# Leitura objetiva de opções (opcoes.net.br) — volume/negócios por CALL/PUT,
# sem volatilidade implícita/gregas (bloqueadas sem login) e sem posição em
# aberto (não exposta neste endpoint). Uso: screening de liquidez, NUNCA
# recomendação de compra/venda.
# ---------------------------------------------------------------------------

OPCOES_URL = "https://opcoes.net.br/listaopcoes/completa"


# ---------------------------------------------------------------------------
# Projeção de juros — Boletim Focus (BCB, via dadosdemercado.com.br)
# ---------------------------------------------------------------------------
# NOTA IMPORTANTE: não existe fonte gratuita confiável para o preço de
# mercado do futuro de DI1 (índice/taxa DI) — nem TradingView (testado
# exaustivamente com vários contratos/bolsas) nem outra fonte pública
# encontrada carregam esse dado sem assinatura paga de market data da B3.
# O Boletim Focus é o melhor substituto público: é a mediana semanal das
# projeções de ~140 analistas/instituições para Selic/IPCA/câmbio/PIB por
# ano — o dado que a imprensa financeira usa como referência de expectativa
# de juros quando não tem acesso à curva de DI em si.

FOCUS_URL = "https://www.dadosdemercado.com.br/boletim-focus"

# Calendário oficial de reuniões do Copom — a decisão sai sempre no 2º dia
# (quarta-feira) de cada reunião. Fonte: divulgação oficial do BCB pra 2026
# (confirmado via InfoMoney/CNN Brasil em set/2026).
# ATUALIZAR quando o BC divulgar o calendário de 2027 — costuma sair em
# dezembro do ano anterior.
COPOM_MEETINGS_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-08-05", "2026-09-16", "2026-11-04", "2026-12-09",
]


def get_copom_calendar() -> dict:
    """Última reunião (passada) e próxima reunião (futura) do Copom, a
    partir de hoje, usando o calendário oficial conhecido."""
    today = datetime.now().date()
    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in COPOM_MEETINGS_2026]
    passadas = [d for d in dates if d <= today]
    futuras = [d for d in dates if d > today]
    return {
        "ultima_reuniao": passadas[-1].isoformat() if passadas else None,
        "proxima_reuniao": futuras[0].isoformat() if futuras else None,
        "dias_ate_proxima": (futuras[0] - today).days if futuras else None,
    }


def get_focus_projections(years: int = 4) -> dict:
    """Extrai do Boletim Focus a projeção de Selic/IPCA/Câmbio por ano-alvo
    (ex.: 2026, 2027, 2028...). Nunca lança exceção — devolve {"error": ...}
    em caso de falha."""
    try:
        resp = requests.get(FOCUS_URL, headers=BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao buscar {FOCUS_URL}: {exc}"}

    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.S)
    text = re.sub("<[^>]+>", "\n", text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    year_re = re.compile(r"^20\d\d$")

    def _to_pct(s: str) -> Optional[float]:
        s = s.replace("%", "").replace(",", ".").strip()
        try:
            return float(s)
        except ValueError:
            return None

    def _to_brl(s: str) -> Optional[float]:
        s = s.replace("R$", "").replace(",", ".").strip()
        try:
            return float(s)
        except ValueError:
            return None

    results = []
    i = 0
    while i < len(lines) and len(results) < years:
        if year_re.match(lines[i]) and i + 1 < len(lines) and lines[i + 1] == "Indicador":
            year = lines[i]
            # a partir daqui, as linhas seguem o padrão:
            # <Nome do indicador> / <há 4 semanas> / <1 sem> / <hoje> / <seta comp.> / <resp.>
            block = {}
            j = i + 2
            while j < len(lines) - 4 and not year_re.match(lines[j]):
                nome = lines[j]
                if nome in ("IPCA", "Selic", "Câmbio", "PIB Total"):
                    ha_4_semanas, uma_semana, hoje = lines[j + 1], lines[j + 2], lines[j + 3]
                    conv = _to_brl if nome == "Câmbio" else _to_pct
                    prefix = "cambio" if nome == "Câmbio" else ("pib" if nome == "PIB Total" else nome.lower())
                    block[f"{prefix}_hoje"] = conv(hoje)
                    block[f"{prefix}_ha_4_semanas"] = conv(ha_4_semanas)
                    block[f"{prefix}_1_semana"] = conv(uma_semana)
                j += 1
            if block:
                results.append({"ano": year, **block})
            i = j
            continue
        i += 1

    if not results:
        return {"error": "não consegui extrair o Boletim Focus (layout pode ter mudado)", "source_url": FOCUS_URL}

    return {"source_url": FOCUS_URL, "projecoes": results}


# Código de mês embutido no ticker da opção (padrão B3): CALL usa A-L,
# PUT usa M-X, cada letra = um mês (A/M=jan ... L/X=dez).
_CALL_MONTH_LETTERS = "ABCDEFGHIJKL"
_PUT_MONTH_LETTERS = "MNOPQRSTUVWX"


def _decode_vencimento(option_ticker: str, tipo: str, root_len: int) -> Optional[tuple[int, int]]:
    """Decodifica (ano, mês) de vencimento a partir do ticker da opção (ex.:
    "PRIOU840_2026", tipo PUT, root_len=4 → 'U' é a letra de mês). Não dá
    pra saber a semana exata (a B3 tem vencimentos semanais dentro do mês
    pra ações líquidas), só o mês — por isso os "prazos" abaixo são uma
    aproximação por mês, não por dia exato."""
    try:
        letter = option_ticker[root_len]
        year = int(option_ticker.split("_")[-1])
        letters = _CALL_MONTH_LETTERS if tipo == "CALL" else _PUT_MONTH_LETTERS
        month = letters.index(letter) + 1
        return year, month
    except (IndexError, ValueError):
        return None


def _prazo_bucket(vencimento: Optional[tuple[int, int]]) -> str:
    """Classifica o vencimento em curto/médio/longo prazo a partir de hoje.
    Aproximação por mês (ver _decode_vencimento) — "curto" cobre desde
    vencimentos desta semana até o fim do mês corrente."""
    if vencimento is None:
        return "desconhecido"
    today = datetime.now()
    delta_meses = (vencimento[0] - today.year) * 12 + (vencimento[1] - today.month)
    if delta_meses <= 1:
        return "curto prazo"
    if delta_meses <= 3:
        return "médio prazo"
    return "longo prazo"


def get_options_summary(ticker: str, top_n: int = 5) -> dict:
    """Busca a grade de opções de `ticker` (ex.: "PRIO3") em opcoes.net.br e
    resume: volume negociado e número de negócios por CALL/PUT, put/call
    ratio (por volume) — geral e separado por dentro/fora do dinheiro
    (ITM/OTM) —, o maior contrato por prazo (curto/médio/longo) e os
    `top_n` contratos mais negociados no dia.

    Não traz Delta/Gama/IV — só dá pra estimar via Black-Scholes, e a
    estimativa não converge de forma confiável nos contratos mais líquidos
    (justamente os que aparecem aqui), então foi removida por decisão do
    usuário em vez de mostrar um número que não presta.
    """
    ticker = ticker.strip().upper()
    try:
        resp = requests.get(
            OPCOES_URL,
            params={"idAcao": ticker, "listarVencimentos": "true", "cotacoes": "true"},
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ticker": ticker, "error": str(exc)}

    data = (body or {}).get("data") or {}
    rows = data.get("cotacoesOpcoes") or []
    if not rows:
        return {"ticker": ticker, "error": "sem dados de opções retornados (ticker sem opções líquidas ou endpoint mudou)"}

    # índices de coluna (ver comentário de cabeçalho do endpoint):
    IDX_TICKER, IDX_TIPO, IDX_MONEY, IDX_STRIKE = 0, 2, 4, 5
    IDX_ULTIMO, IDX_NUM_NEG, IDX_VOL = 8, 9, 10
    IDX_IV = 12  # geralmente bloqueado (imagem) sem login

    root_len = len(ticker.rstrip("0123456789"))

    traded = [r for r in rows if r[IDX_NUM_NEG]]  # só contratos com negócio no dia
    calls = [r for r in traded if r[IDX_TIPO] == "CALL"]
    puts = [r for r in traded if r[IDX_TIPO] == "PUT"]

    vol_calls = sum(r[IDX_VOL] or 0 for r in calls)
    vol_puts = sum(r[IDX_VOL] or 0 for r in puts)
    neg_calls = sum(r[IDX_NUM_NEG] or 0 for r in calls)
    neg_puts = sum(r[IDX_NUM_NEG] or 0 for r in puts)

    put_call_ratio_volume = round(vol_puts / vol_calls, 3) if vol_calls else None

    # ---- ratio separado por dentro (ITM) / fora (OTM) do dinheiro ----
    # ITM = já valeria a pena exercer hoje; OTM = só vale se o preço se
    # mover até lá — normalmente mais especulativo/hedge barato, volume
    # OTM alto costuma pesar mais numa leitura de "aposta direcional" do
    # que volume ITM (que pode ser rolagem/ajuste de posição existente).
    def _vol(rows_, tipo, money):
        return sum(r[IDX_VOL] or 0 for r in rows_ if r[IDX_TIPO] == tipo and r[IDX_MONEY] == money)

    vol_calls_itm, vol_calls_otm = _vol(traded, "CALL", "ITM"), _vol(traded, "CALL", "OTM")
    vol_puts_itm, vol_puts_otm = _vol(traded, "PUT", "ITM"), _vol(traded, "PUT", "OTM")
    put_call_ratio_itm = round(vol_puts_itm / vol_calls_itm, 3) if vol_calls_itm else None
    put_call_ratio_otm = round(vol_puts_otm / vol_calls_otm, 3) if vol_calls_otm else None

    iv_disponivel = not any(isinstance(r[IDX_IV], str) and "volblur" in r[IDX_IV] for r in traded[:5])

    def _fmt(r) -> dict:
        vencimento = _decode_vencimento(r[IDX_TICKER], r[IDX_TIPO], root_len)
        return {
            "ticker": r[IDX_TICKER],
            "tipo": r[IDX_TIPO],
            "strike": r[IDX_STRIKE],
            "moneyness": r[IDX_MONEY],
            "ultimo": r[IDX_ULTIMO],
            "num_negocios": r[IDX_NUM_NEG],
            "volume_financeiro": r[IDX_VOL],
            "vencimento_aprox": f"{vencimento[0]}-{vencimento[1]:02d}" if vencimento else None,
            "prazo": _prazo_bucket(vencimento),
        }

    top = sorted(traded, key=lambda r: (r[IDX_VOL] or 0), reverse=True)[:top_n]
    top_fmt = [_fmt(r) for r in top]

    # maior contrato absoluto por prazo (curto/médio/longo) — pra achar o
    # destaque de volume "no curto prazo" e "no médio prazo" separados.
    maior_por_prazo: dict[str, dict] = {}
    for r in sorted(traded, key=lambda r: (r[IDX_VOL] or 0), reverse=True):
        vencimento = _decode_vencimento(r[IDX_TICKER], r[IDX_TIPO], root_len)
        prazo = _prazo_bucket(vencimento)
        if prazo not in maior_por_prazo:
            maior_por_prazo[prazo] = _fmt(r)

    return {
        "ticker": ticker,
        "contratos_negociados_hoje": len(traded),
        "volume_financeiro_calls": vol_calls,
        "volume_financeiro_puts": vol_puts,
        "negocios_calls": neg_calls,
        "negocios_puts": neg_puts,
        "put_call_ratio_volume": put_call_ratio_volume,
        "put_call_ratio_itm": put_call_ratio_itm,
        "put_call_ratio_otm": put_call_ratio_otm,
        "volume_calls_itm": vol_calls_itm,
        "volume_calls_otm": vol_calls_otm,
        "volume_puts_itm": vol_puts_itm,
        "volume_puts_otm": vol_puts_otm,
        "maior_contrato_por_prazo": maior_por_prazo,
        "volatilidade_implicita_disponivel": iv_disponivel,
        "top_mais_negociados": top_fmt,
        "nota": "Dados de liquidez/atividade, não é recomendação de compra/venda. Posição em aberto e gregas não vêm neste endpoint sem login.",
    }
