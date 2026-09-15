# Trade Hunter — agente informativo de mercado B3

Monitor automático de ações, FIIs e índices da B3 + referências globais, com
alertas via Telegram e Discord. Roda **100% na nuvem via GitHub Actions** —
não depende do seu Mac ligado, do Claude Desktop aberto, nem de nenhum
processo local. Também dá pra usar como servidor MCP (`server.py`) dentro do
Claude Desktop/Code, como modo opcional separado.

**Regra de compliance**: nada aqui gera recomendação de compra/venda. Os
scripts só organizam dados objetivos (preço, variação, fluxo estrangeiro,
volume/put-call ratio de opções, projeção de juros) para quem opera decidir
por conta própria.

## Estrutura

```
repositoryWG/
├── .github/workflows/
│   ├── intraday.yml       # agenda + roda watch.py --mode intraday
│   └── daily.yml          # agenda + roda watch.py --mode daily
└── trade-hunter/
    ├── watch.py           # orquestrador principal — 3 modos (ver abaixo)
    ├── market_data.py     # cotações (TradingView), fluxo estrangeiro,
    │                      # opções (opcoes.net.br) + Black-Scholes estimado,
    │                      # Boletim Focus/BCB + calendário do Copom
    ├── discord_alert.py   # envio via Discord Webhook
    ├── telegram_alert.py  # envio via Telegram Bot API
    ├── server.py          # servidor MCP opcional (tool-calling nativo — exige Python 3.10+)
    ├── sources.py         # cotações TradingView/Google Finance (usado pelo server.py)
    ├── storage.py         # persistência de alertas (usado pelo server.py)
    ├── requirements.txt
    ├── .env.example
    └── README.md
```

## O que é monitorado

- **12 ações**: MGLU3, LREN3, PRIO3, BBAS3, BRAV3, PETR4, BBSE3, ITSA4, SUZB3, BBDC4, CMIG4, SAPR11
- **9 FIIs**: ALZR11, CPTS11, GGRC11, HGLG11, LVBI11, RBRY11, RECR11, RZTR11, VRTA11
- **Índices/referências**: IBOV, IBXL (IBrX-50), VIX, Brent, WTI, S&P 500, Dow, Nasdaq, FTSE, DAX, CAC40, Euro Stoxx, Nikkei, Hang Seng, Xangai, Kospi, Merval, Mexbol, USD/BRL, EUR/BRL, DXY, ETFs (IEF, TLT, VWO, EWZ), futuros (E-mini S&P/Dow/Nasdaq, ouro)
- **Opções** (opcoes.net.br): volume/negócios por CALL/PUT, put/call ratio geral e por dentro/fora do dinheiro (ITM/OTM), maior contrato por prazo (curto/médio/longo), Delta/Gama/IV estimados via Black-Scholes (a B3 bloqueia o valor real sem assinatura paga)
- **Fluxo de investidor estrangeiro** (dadosdemercado.com.br, público)
- **Projeção de juros** (Boletim Focus/BCB) + calendário oficial do Copom

## watch.py — os 3 modos

```bash
python3 watch.py --mode intraday   # checa limiares de variação + notícias novas;
                                    # só manda push se algo relevante mudou
python3 watch.py --mode daily      # sempre manda 1 resumo completo
                                    # (painel global, opções, fluxo, Focus)
python3 watch.py --mode screen     # imprime o relatório completo no terminal,
                                    # sem enviar nada — bom pra consulta manual
```

Ambos os modos que enviam (`intraday`/`daily`) mandam para Telegram e Discord
(o que estiver configurado — nenhum dos dois é obrigatório sozinho).

## Automação em produção: GitHub Actions (nuvem, custo zero)

Todo o agendamento roda em **dois workflows do GitHub Actions**, no repositório
público — sem depender de nenhuma máquina local ligada:

| Workflow | Agenda (cron, UTC) | Equivalente em BRT | O que faz |
|---|---|---|---|
| `.github/workflows/intraday.yml` | `*/15 13-20 * * 1-5` | a cada 15 min, 10h–17h45, dias úteis | `watch.py --mode intraday` — só notifica se algo relevante mudou (ou nos 3 horários fixos de panorama: 10h/13h/16h) |
| `.github/workflows/daily.yml` | `0 15 * * 1-5`, `0 18 * * 1-5`, `15 21 * * 1-5` | 12h, 15h, 18h15, dias úteis | `watch.py --mode daily` — sempre manda o resumo completo |

Detalhes de implementação:

- **`workflow_dispatch`** habilitado nos dois — dá pra disparar manualmente
  pela aba Actions do GitHub, sem esperar o horário agendado (útil pra testar).
- **Fuso horário**: `env: TZ: America/Sao_Paulo` em cada job, porque o
  runner do GitHub roda em UTC por padrão e o `watch.py` decide
  dia-útil/horário-de-pregão com base em `datetime.now()`.
- **Estado entre execuções** (`trade-hunter/data/`) persiste via
  `actions/cache/restore` + `actions/cache/save`, com chave por
  `github.run_id` e prefixo de `restore-keys` — cada workflow tem seu
  próprio namespace (`th-intraday-state-` / `th-daily-state-`) pra não haver
  cross-talk entre os dois.
- **Segredos**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e
  `DISCORD_WEBHOOK_URL` ficam em *Settings → Secrets and variables →
  Actions* do repositório — nunca aparecem no código nem nos logs.
- **Alerta de falha**: se o job quebrar, um passo `if: failure()` manda uma
  mensagem no Telegram com o link direto pro run que falhou.
- **Custo**: **zero** — GitHub Actions é ilimitado para repositórios
  públicos (não consome os 2.000 min/mês do plano gratuito de repo privado).

Não existe mais nenhuma dependência de `launchd`, cron local, ou qualquer
processo rodando no seu Mac — o robô roda sozinho na infraestrutura do
GitHub, 24 horas por dia, independente do computador estar ligado ou não.

## Rodar localmente (opcional, só para teste/debug)

O `watch.py`/`market_data.py` usam só `requests`, `beautifulsoup4` e
`python-dotenv` — rodam com o Python do sistema (3.9+), sem precisar de venv:

```bash
pip3 install --user requests beautifulsoup4 python-dotenv
cp .env.example .env
```

Preencha o `.env` (mesmas variáveis dos secrets do GitHub Actions, só que
para teste local):

```
TELEGRAM_BOT_TOKEN=123456789:AAExxxxx...
TELEGRAM_CHAT_ID=987654321
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

- **Telegram**: fale com `@BotFather`, envie `/newbot`, guarde o token;
  mande uma mensagem para o bot recém-criado; pegue o `chat_id` em
  `https://api.telegram.org/bot<TOKEN>/getUpdates`.
- **Discord**: no canal desejado → *Configurações do canal → Integrações →
  Webhooks → Novo Webhook → Copiar URL do Webhook*.

Depois é só rodar qualquer um dos 3 modos do `watch.py` acima. Isso é útil
pra debugar ou ver o relatório na hora (`--mode screen`), mas **não é
necessário para a rotina em produção**, que já roda sozinha no GitHub Actions.

## (Opcional) Usar como MCP dentro do Claude

Modo separado, não relacionado à automação em nuvem acima — exige Python
3.10+ (o pacote `mcp` não roda em 3.9).

```bash
pip install mcp requests beautifulsoup4 python-dotenv
claude mcp add trade-hunter -- python3 /caminho/completo/para/trade-hunter/server.py
```

Ou no Claude Desktop, em `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trade-hunter": {
      "command": "python3",
      "args": ["/caminho/completo/para/trade-hunter/server.py"]
    }
  }
}
```

Ferramentas expostas: `get_quote`, `add_price_alert`, `list_price_alerts`,
`remove_price_alert`, `check_alerts`, `send_test_telegram_message`.

## Limitações conhecidas

- **DI1 (juros futuros B3)**: sem fonte gratuita em tempo real (nem
  TradingView nem outra fonte pública) — usamos o Boletim Focus/BCB como
  proxy (mediana semanal de ~140 analistas), não é o preço de mercado real
  do contrato.
- **IBrX-100 (IBXX)**: não existe no scanner gratuito do TradingView — o
  parente mais próximo disponível é o IBrX-50 (IBXL).
- **Delta/Gama/IV de opções**: opcoes.net.br bloqueia esses campos sem
  assinatura paga (vêm como `null`). O que aparece no relatório é uma
  estimativa própria via Black-Scholes (a partir do preço realmente
  negociado), sinalizada como não confiável quando o contrato está muito
  dentro/fora do dinheiro perto do vencimento (regime onde o modelo fica
  mal condicionado).
- **Vencimento de opção**: decodificado a partir do ticker (mês, não dia —
  a B3 tem vencimentos semanais pra ações líquidas).
- **Google Finance** (usado só por `server.py`/`sources.py`): scraping
  best-effort, pode quebrar se o Google mudar o HTML.
- **TradingView**: endpoint público não-oficial do scanner — uso
  pessoal/moderado tende a funcionar bem, evite volume alto de chamadas.
- **Cron do GitHub Actions**: granularidade mínima de ~5 minutos e sujeita
  a atraso de alguns minutos em horários de pico da infraestrutura do
  GitHub — o `watch.py` compensa isso checando por janela de tempo, não por
  minuto exato.
