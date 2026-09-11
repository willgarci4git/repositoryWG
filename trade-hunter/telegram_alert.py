"""Envio de alertas via Telegram Bot API (sem dependências além de `requests`)."""

from __future__ import annotations

import os
from typing import Optional

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str, parse_mode: Optional[str] = "Markdown") -> dict:
    """Envia uma mensagem para o chat configurado via TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.

    `parse_mode`: "Markdown" (legado — só aceita *negrito*/_itálico_ de
    asterisco/underscore único; QUALQUER um desencontrado no texto derruba
    a mensagem inteira com erro 400), ou None para texto puro (mais seguro
    para relatórios longos/gerados dinamicamente, onde não dá pra garantir
    que cada marcador vai vir em par).

    Retorna {"ok": True} em sucesso, ou {"ok": False, "error": "..."} caso
    falte configuração ou a chamada falhe — nunca lança exceção, para não
    derrubar o restante do check_alerts.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return {
            "ok": False,
            "error": (
                "TELEGRAM_BOT_TOKEN e/ou TELEGRAM_CHAT_ID não configurados "
                "(veja o .env.example / README)."
            ),
        }

    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    url = TELEGRAM_API.format(token=token)
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def format_alert_message(ticker: str, target_price: float, direction: str, price: float, note: Optional[str] = None) -> str:
    arrow = "📈" if direction == "above" else "📉"
    direction_txt = "atingiu ou superou" if direction == "above" else "caiu para ou abaixo de"
    msg = (
        f"{arrow} *Trade Hunter — alerta disparado*\n"
        f"*{ticker}* {direction_txt} R$ {target_price:,.2f}\n"
        f"Preço atual (TradingView): R$ {price:,.2f}"
    )
    if note:
        msg += f"\nNota: {note}"
    return msg
