"""
Trade Hunter — MCP server

Expõe ferramentas de cotação (TradingView + Google Finance como referência) e
alertas de preço para ações da B3, com notificação via Telegram, para uso
dentro do Claude (Claude Desktop / Claude Code) via MCP.

Rodar localmente:
    python3 server.py

Configurar no Claude Desktop (claude_desktop_config.json) ou no Claude Code
(claude mcp add), apontando para este arquivo. Veja README.md.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import storage  # noqa: E402
from sources import get_reference_quotes, get_tradingview_quote  # noqa: E402
from telegram_alert import format_alert_message, send_telegram_message  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

mcp = FastMCP("trade-hunter")


@mcp.tool()
def get_quote(ticker: str) -> dict:
    """Busca a cotação atual de uma ação da B3.

    Usa o TradingView como fonte primária (mais confiável para os alertas) e
    traz o Google Finance como referência secundária para comparação.

    Args:
        ticker: código da ação na B3, ex. "PETR4", "VALE3", "ITUB4" (sem .SA).
    """
    return get_reference_quotes(ticker)


@mcp.tool()
def add_price_alert(ticker: str, target_price: float, direction: str, note: str = "") -> dict:
    """Cria um alerta de preço para uma ação da B3.

    Args:
        ticker: código da ação, ex. "PETR4".
        target_price: preço-gatilho, em R$.
        direction: "above" para disparar quando o preço subir até/acima do
            alvo, ou "below" para disparar quando cair até/abaixo do alvo.
        note: observação livre (opcional), ex. "preço de entrada planejado".
    """
    alert = storage.add_alert(ticker, target_price, direction, note)
    return {"created": True, "alert": alert}


@mcp.tool()
def list_price_alerts(status: str = "active") -> dict:
    """Lista os alertas de preço cadastrados.

    Args:
        status: "active", "triggered", "removed" ou "all".
    """
    status_filter = None if status == "all" else status
    alerts = storage.list_alerts(status_filter)
    return {"count": len(alerts), "alerts": alerts}


@mcp.tool()
def remove_price_alert(alert_id: str) -> dict:
    """Remove (desativa) um alerta de preço pelo seu id.

    Args:
        alert_id: id retornado por add_price_alert ou list_price_alerts.
    """
    ok = storage.remove_alert(alert_id)
    return {"removed": ok}


@mcp.tool()
def check_alerts(send_telegram: bool = True) -> dict:
    """Verifica todos os alertas ativos contra o preço atual (TradingView) e
    dispara notificação no Telegram para os que forem atingidos.

    Chame esta ferramenta periodicamente (manualmente pelo Claude, ou por uma
    automação externa) para efetivamente monitorar os preços — o MCP server
    não roda checagens sozinho em segundo plano.

    Args:
        send_telegram: se False, só reporta quais alertas bateriam o gatilho,
            sem enviar mensagem no Telegram (modo "dry run").
    """
    active = storage.list_alerts("active")
    results = []

    # Evita buscar a cotação do mesmo ticker mais de uma vez nesta checagem.
    price_cache: dict[str, float | None] = {}

    for alert in active:
        ticker = alert["ticker"]
        if ticker not in price_cache:
            quote = get_tradingview_quote(ticker)
            price_cache[ticker] = quote.price
            if quote.error:
                results.append(
                    {
                        "alert_id": alert["id"],
                        "ticker": ticker,
                        "status": "error",
                        "error": quote.error,
                    }
                )
                continue

        price = price_cache[ticker]
        if price is None:
            continue

        storage.update_last_checked(alert["id"], price)

        hit = (alert["direction"] == "above" and price >= alert["target_price"]) or (
            alert["direction"] == "below" and price <= alert["target_price"]
        )

        if hit:
            telegram_result = {"ok": False, "error": "não enviado (dry run)"}
            if send_telegram:
                message = format_alert_message(
                    ticker, alert["target_price"], alert["direction"], price, alert.get("note")
                )
                telegram_result = send_telegram_message(message)
            storage.mark_triggered(alert["id"], price)
            results.append(
                {
                    "alert_id": alert["id"],
                    "ticker": ticker,
                    "status": "triggered",
                    "price": price,
                    "target_price": alert["target_price"],
                    "telegram": telegram_result,
                }
            )
        else:
            results.append(
                {
                    "alert_id": alert["id"],
                    "ticker": ticker,
                    "status": "not_triggered",
                    "price": price,
                    "target_price": alert["target_price"],
                }
            )

    return {"checked": len(active), "results": results}


@mcp.tool()
def send_test_telegram_message(text: str = "🔔 Trade Hunter conectado com sucesso!") -> dict:
    """Envia uma mensagem de teste ao Telegram, para validar a configuração
    de TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
    """
    return send_telegram_message(text)


if __name__ == "__main__":
    mcp.run()
