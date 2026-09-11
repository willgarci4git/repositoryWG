"""Envio de alertas via Discord Webhook (sem dependências além de `requests`).

Mesmo padrão usado no monitor de voos (flight-price-watch/check_flights.py):
um Webhook de canal, POST simples, sem precisar de bot/OAuth.
"""

from __future__ import annotations

import os

import requests


def send_discord_message(text: str) -> dict:
    """Envia uma mensagem pro canal configurado via DISCORD_WEBHOOK_URL.

    Quebra em pedaços de até 1900 caracteres (o Discord corta em 2000).
    Retorna {"ok": True} em sucesso, ou {"ok": False, "error": "..."} caso
    falte configuração ou a chamada falhe — nunca lança exceção.
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return {"ok": False, "error": "DISCORD_WEBHOOK_URL não configurado (veja o .env.example / README)."}

    chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [text]
    for chunk in chunks:
        try:
            resp = requests.post(webhook_url, json={"content": chunk}, timeout=15)
            # Discord responde 204 No Content em sucesso
            if resp.status_code not in (200, 204):
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
    return {"ok": True}
