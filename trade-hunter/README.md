# Trade Hunter — agente informativo de mercado B3

Monitor automático (roda via `launchd`, independente do Claude Desktop
aberto) de ações, FIIs e índices da B3 + referências globais, com alertas
via **Telegram** e **Discord**. Também dá pra usar como servidor MCP
(`server.py`) dentro do Claude Desktop/Code.

**Regra de compliance:** nada aqui gera recomendação de compra/venda. Os
scripts só organizam dados objetivos (preço, variação, fluxo estrangeiro,
volume/put-call ratio de opções, projeção de juros) para quem opera decidir
por conta própria.

## Estrutura

```
trade-hunter/
├── watch.py             # orquestrador principal — 3 modos (ver abaixo)
├── market_data.py        # cotações (TradingView), fluxo estrangeiro,
│                          # opções (opcoes.net.br) + Black-Scholes estimado,
│                          # Boletim Focus/BCB + calendário do Copom
├── discord_alert.py      # envio via Discord Webhook
├── telegram_alert.py     # envio via Telegram Bot API
├── server.py             # servidor MCP (tool-calling nativo — exige Python 3.10+)
├── sources.py             # cotações TradingView/Google Finance (usado pelo server.py)
├── storage.py              # persistência de alertas (usado pelo server.py)
├── requirements.txt
├── .env.example
└── README.md
```

## O que é monitorado

- **12 ações**: MGLU3, LREN3, PRIO3, BBAS3, BRAV3, PETR4, BBSE3, ITSA4,
  SUZB3, BBDC4, CMIG4, SAPR11
- **9 FIIs**: ALZR11, CPTS11, GGRC11, HGLG11, LVBI11, RBRY11, RECR11,
  RZTR11, VRTA11
- **Índices/referências**: IBOV, IBXL (IBrX-50), VIX, Brent, WTI, S&P 500,
  Dow, Nasdaq, FTSE, DAX, CAC40, Euro Stoxx, Nikkei, Hang Seng, Xangai,
  Kospi, Merval, Mexbol, USD/BRL, EUR/BRL, DXY, ETFs (IEF, TLT, VWO, EWZ),
  futuros (E-mini S&P/Dow/Nasdaq, ouro)
- **Opções** (opcoes.net.br): volume/negócios por CALL/PUT, put/call ratio
  geral e por dentro/fora do dinheiro (ITM/OTM), maior contrato por prazo
  (curto/médio/longo), Delta/Gama/IV **estimados** via Black-Scholes (a B3
  bloqueia o valor real sem login)
- **Fluxo de investidor estrangeiro** (dadosdemercado.com.br, público)
- **Projeção de juros** (Boletim Focus/BCB) + calendário oficial do Copom

## `watch.py` — os 3 modos

```bash
python3 watch.py --mode intraday   # checa limiares de variação + notícias novas;
                                     # só manda push se algo relevante mudou
python3 watch.py --mode daily       # sempre manda 1 resumo completo
                                     # (painel global, opções, fluxo, Focus)
python3 watch.py --mode screen      # imprime o relatório completo no terminal,
                                     # sem enviar nada — bom pra consulta manual
```

Ambos os modos mandam para **Telegram e Discord** (o que estiver configurado
no `.env` — nenhum dos dois é obrigatório sozinho).

## 1. Instalar dependências

O `watch.py`/`market_data.py` usam só `requests`, `beautifulsoup4` e
`python-dotenv` — rodam com o Python do sistema (3.9+), sem precisar de
venv:

```bash
pip3 install --user requests beautifulsoup4 python-dotenv
```

(`server.py`, o servidor MCP com tool-calling nativo, precisa do pacote
`mcp`, que exige **Python 3.10+** — opcional, só se você quiser usar dentro
do Claude via MCP em vez dos scripts.)

## 2. Configurar Telegram e/ou Discord

```bash
cp .env.example .env
```

**Telegram:**
1. Fale com **@BotFather**, envie `/newbot`, guarde o token.
2. Mande uma mensagem para o bot recém-criado.
3. Pegue o `chat_id` em `https://api.telegram.org/bot<TOKEN>/getUpdates`.

**Discord:**
1. No canal desejado: Configurações do canal → Integrações → Webhooks →
   Novo Webhook → Copiar URL do Webhook.

Preencha o `.env`:

```
TELEGRAM_BOT_TOKEN=123456789:AAExxxxx...
TELEGRAM_CHAT_ID=987654321
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## 3. Automatizar (independente do app aberto)

Recomendado: **`launchd`** (nativo do macOS), rodando sozinho mesmo com o
Claude Desktop fechado — só depende do Mac estar ligado e você logado.

Dois `LaunchAgent`s em `~/Library/LaunchAgents/`:

- `com.tradehunter.intraday.plist` — `StartInterval` de 900s (15 min),
  24/7; o próprio `watch.py` filtra dia útil + horário de pregão (10h-17h)
  e sai sem fazer nada fora disso.
- `com.tradehunter.daily.plist` — `StartCalendarInterval` em 3 horários
  fixos (12h, 15h, 18h15); pula sozinho em fim de semana.

Ambos com `ProgramArguments` apontando pro Python do sistema + `watch.py`
com o `--mode` correspondente, `WorkingDirectory` na pasta do projeto, e
logs em `logs/*.log` / `logs/*.err.log`.

**Importante:** rode o projeto de uma pasta que **não** seja
`~/Documents`, `~/Desktop` ou `~/Downloads` — o macOS bloqueia acesso de
processos do `launchd` a essas pastas por privacidade (erro
`Operation not permitted`). `~/trade-hunter` (fora dessas pastas) funciona
sem pedir nada.

Carregar:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradehunter.intraday.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradehunter.daily.plist
launchctl kickstart -k gui/$(id -u)/com.tradehunter.intraday   # forçar rodar agora
```

Alternativa: usar `server.py` como MCP dentro do Claude (ver seção 4)
e pedir pro Claude chamar `check_alerts` periodicamente — mas isso só
funciona com uma sessão do Claude aberta o tempo todo.

## 4. (Opcional) Usar como MCP dentro do Claude

Exige Python **3.10+** (o pacote `mcp` não roda em 3.9).

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
  TradingView nem outra fonte pública) — usamos o **Boletim Focus/BCB**
  como proxy (mediana semanal de ~140 analistas), não é o preço de mercado
  real do contrato.
- **IBrX-100 (IBXX)**: não existe no scanner gratuito do TradingView — o
  parente mais próximo disponível é o **IBrX-50 (IBXL)**.
- **Delta/Gama/IV de opções**: `opcoes.net.br` bloqueia esses campos sem
  login (vêm como imagem borrada). O que aparece no relatório é uma
  **estimativa própria via Black-Scholes** (a partir do preço realmente
  negociado), sinalizada como não confiável quando o contrato está muito
  dentro/fora do dinheiro perto do vencimento (regime onde o modelo fica
  mal condicionado).
- **Vencimento de opção**: decodificado a partir do ticker (mês, não dia —
  a B3 tem vencimentos semanais pra ações líquidas).
- **Google Finance** (usado só por `server.py`/`sources.py`): scraping
  best-effort, pode quebrar se o Google mudar o HTML.
- **TradingView**: endpoint público não-oficial do scanner — uso
  pessoal/moderado tende a funcionar bem, evite volume alto de chamadas.
