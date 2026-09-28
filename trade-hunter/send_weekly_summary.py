#!/usr/bin/env python3
"""
Trade Hunter — envia o Resumo Semanal (Panorama da Semana / Agenda Macro)
para Telegram + Discord.

Como funciona: uma rotina na nuvem (Claude Code cloud routine, roda toda
segunda-feira às 9h BRT, consumindo o plano do usuário — não uma API paga
separada) pesquisa a agenda macro da semana e escreve o texto em
`resumo_semanal_pendente.md`, nesta mesma pasta, e dá `git push`. Esse push
é o gatilho do workflow `.github/workflows/weekly-summary.yml`, que roda
este script pra mandar o conteúdo do arquivo pros canais configurados.

Reaproveita o envio/chunking já usado no resumo diário (watch.py) — nada
de lógica de envio duplicada.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from watch import _send_to_all_channels, _channel_errors, _chunk_for_telegram  # noqa: E402

SUMMARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resumo_semanal_pendente.md")


def main() -> int:
    if not os.path.exists(SUMMARY_FILE):
        print(f"AVISO: {SUMMARY_FILE} não existe — nada a enviar.", file=sys.stderr)
        return 1

    with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        print("AVISO: arquivo do resumo semanal está vazio.", file=sys.stderr)
        return 1

    # mesmo limite de segurança do resumo diário (4096 é o teto real do
    # Telegram; 3800 deixa folga) — normalmente o resumo semanal cabe
    # num chunk só, mas protege se algum dia vier maior.
    chunks = _chunk_for_telegram(text) if len(text) > 3800 else [text]

    for i, chunk in enumerate(chunks):
        # parse_mode=None: o texto vem com **negrito**/_itálico_ estilo
        # Markdown "normal" (escrito pela rotina), que não é o dialeto que
        # a API legada do Telegram aceita — mais seguro não tentar formatar.
        results = _send_to_all_channels(chunk, parse_mode=None)
        erros = _channel_errors(results)
        if erros:
            print(f"AVISO: falha ao enviar (parte {i + 1}/{len(chunks)}) — {'; '.join(erros)}", file=sys.stderr)
            return 1

    print("Resumo semanal enviado com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
