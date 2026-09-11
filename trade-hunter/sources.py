"""
Fontes de cotação para o Trade Hunter.

- TradingView: fonte primária, usada para confiar em alertas de preço.
  Replica a chamada HTTP que a lib `tradingview-ta` faz (scanner.tradingview.com),
  sem depender do pacote (que pode não estar disponível em alguns ambientes
  restritos de rede).
- Google Finance: fonte de referência/cross-check (best-effort scraping).
  A estrutura HTML do Google muda sem aviso, então essa função nunca deve ser
  a única fonte usada para disparar um alerta — só para comparação visual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests

TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/{screener}/scan"
DEFAULT_HEADERS = {
    "User-Agent": "trade-hunter-mcp/1.0 (+https://github.com/)",
    "Content-Type": "application/json",
}

# Colunas pedidas ao scanner do TradingView, na ordem em que os valores voltam.
TV_COLUMNS = [
    "close",
    "open",
    "high",
    "low",
    "volume",
    "change",
    "change_abs",
    "Recommend.All",
    "RSI",
]


@dataclass
class Quote:
    source: str
    symbol: str
    price: Optional[float]
    change_pct: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    extra: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "symbol": self.symbol,
            "price": self.price,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
        }
        if self.extra:
            d["extra"] = self.extra
        if self.error:
            d["error"] = self.error
        return d


def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".SA", "")


def get_tradingview_quote(
    ticker: str, exchange: str = "BMFBOVESPA", screener: str = "brazil", timeout: int = 10
) -> Quote:
    """Busca a cotação atual de um ticker via TradingView (fonte primária).

    ticker: código da ação, ex. "PETR4", "VALE3" (sem sufixo .SA)
    exchange: bolsa no formato TradingView, padrão B3 = "BMFBOVESPA"
    screener: país/screener do TradingView, padrão "brazil"
    """
    symbol = _normalize_ticker(ticker)
    tv_symbol = f"{exchange}:{symbol}"
    url = TRADINGVIEW_SCAN_URL.format(screener=screener.lower())
    payload = {
        "symbols": {"tickers": [tv_symbol], "query": {"types": []}},
        "columns": TV_COLUMNS,
    }

    try:
        resp = requests.post(url, json=payload, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") or []
        if not data:
            return Quote(
                source="tradingview",
                symbol=tv_symbol,
                price=None,
                error=f"Símbolo '{tv_symbol}' não encontrado no TradingView.",
            )
        values = data[0].get("d") or []
        row = dict(zip(TV_COLUMNS, values))
        return Quote(
            source="tradingview",
            symbol=tv_symbol,
            price=row.get("close"),
            change_pct=row.get("change"),
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            volume=row.get("volume"),
            extra={"recommend_all": row.get("Recommend.All"), "rsi": row.get("RSI")},
        )
    except Exception as exc:  # noqa: BLE001 - queremos devolver o erro, não derrubar o servidor
        return Quote(source="tradingview", symbol=tv_symbol, price=None, error=str(exc))


def get_google_finance_quote(ticker: str, exchange: str = "BVMF", timeout: int = 10) -> Quote:
    """Busca a cotação atual de um ticker no Google Finance (fonte de referência).

    Faz scraping best-effort da página pública — sem chave de API. Se o Google
    mudar o HTML, essa função retorna Quote com `error` preenchido em vez de
    quebrar o servidor.
    """
    from bs4 import BeautifulSoup

    symbol = _normalize_ticker(ticker)
    gf_symbol = f"{symbol}:{exchange}"
    url = f"https://www.google.com/finance/quote/{gf_symbol}"

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; trade-hunter-mcp/1.0)"},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Seletor conhecido do preço principal no Google Finance (pode mudar).
        price_el = soup.find("div", {"class": re.compile(r"\bYMlKec\b")})
        price = _parse_brl_number(price_el.text) if price_el else None

        change_pct = None
        change_el = soup.find("div", {"class": re.compile(r"\bJwB6zf\b")})
        if change_el:
            change_pct = _parse_pct(change_el.text)

        if price is None:
            return Quote(
                source="google_finance",
                symbol=gf_symbol,
                price=None,
                error=(
                    "Não consegui extrair o preço da página do Google Finance "
                    "(layout pode ter mudado). Use como referência manual: " + url
                ),
            )

        return Quote(
            source="google_finance",
            symbol=gf_symbol,
            price=price,
            change_pct=change_pct,
            extra={"url": url},
        )
    except Exception as exc:  # noqa: BLE001
        return Quote(source="google_finance", symbol=gf_symbol, price=None, error=str(exc))


def _parse_brl_number(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.strip().replace("R$", "").replace("US$", "").strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    match = re.search(r"-?\d+(\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _parse_pct(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"-?\d+(\.\d+)?", text.replace(",", "."))
    return float(match.group()) if match else None


def get_reference_quotes(ticker: str) -> dict:
    """Retorna cotações do TradingView (primária) e Google Finance (referência)."""
    tv = get_tradingview_quote(ticker)
    gf = get_google_finance_quote(ticker)
    return {"tradingview": tv.to_dict(), "google_finance": gf.to_dict()}
