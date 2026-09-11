# Monitor de preços de voos — 28 rotas

## O que isso faz
Toda **segunda-feira ~8h** (tarefa agendada "flight-price-watch", roda enquanto o
Claude Code estiver aberto — se estiver fechado no horário, roda na próxima
abertura), o script `check_flights.py`:

1. Busca o preço mais barato de cada rota dentro da janela configurada
   (`window_months` no `config.json`), ida e volta de **10-30 dias**,
   classe econômica, 1 passageiro, em **reais**, usando a API grátis da
   **Travelpayouts** (calendário de preços).
2. Para rotas sem dado na Travelpayouts (comum em destinos menos
   populares), faz 1 consulta de amostra direto no **SerpAPI (Google
   Flights)**, revezando o mês verificado a cada semana — **com cadência
   por prioridade**: rotas com voo direto confirmado (`verified_nonstop:
   true`) são checadas via SerpAPI toda semana; rotas sem direto
   confirmado (`verified_nonstop: false`) só a cada 2 semanas, pra
   economizar cota.
3. Compara o preço desta semana com o histórico local (`price_history.json`)
   de cada rota. Considera "queda de preço" quando o valor é o **menor já
   visto** para aquela rota, ou está **15%+ abaixo da média recente**
   (e só repete o alerta depois de 21 dias, ou se cair ainda mais).
4. Quando acha uma queda, gasta **1 chamada extra do SerpAPI** pra confirmar
   o preço ao vivo (bookable) antes de te avisar.
5. Se achou alguma queda (ou menção editorial), **envia alerta**: sempre em
   **Discord** (canal #buscador-de-passagens, servidor Mercado Tech) e
   **Telegram** (grupo "Alerta Passagens WG") — enviados diretamente pelo
   script via HTTP. O **e-mail** (`mercadotechbrasilwgx@gmail.com`, via
   Gmail na tarefa agendada) é controlado pela flag `email_enabled` no
   `config.json` — **está desativado (`false`) por pedido do usuário**,
   mas toda a lógica continua pronta: é só mudar para `true` pra
   reativar, sem precisar recriar nada. Se não achou nada, não manda nada
   em nenhum canal (sem spam semanal).

## Rotas monitoradas (28 no total)

**18 rotas com voo direto (non-stop) confirmado** — `verified_nonstop: true`
no config, checadas toda semana, com companhias e aviso de risco de
operador único:
- **De GRU**: Lisboa, Porto*, Madri, Barcelona*, Paris, Amsterdã, Bruxelas*,
  Frankfurt, Munique*, Zurique*, Londres, Roma, Milão*, Istambul*, África do
  Sul (Joanesburgo), Canadá (Toronto)*
- **Fernando de Noronha**: de Guarulhos (GRU) e de Viracopos (VCP)*

  *\* = operador único (single_carrier) — sem alternativa direta em caso de
  cancelamento/remanejo.*

**10 rotas sem non-stop confirmado** — `verified_nonstop: false`, checadas
via SerpAPI a cada 2 semanas (Travelpayouts continua sendo checada toda
semana, é grátis): Cairo, Tel Aviv, Marrocos (Casablanca/Marrakech),
Bolívia (La Paz/Santa Cruz), Islândia, Japão (Tóquio), Quênia (Nairóbi),
Grécia (Atenas), Polônia (Varsóvia), Fernando de Noronha via Congonhas
(CGH — sem direto desde 2022).

O e-mail/Discord/Telegram nunca chamam uma tarifa de "direto" sem checar o
número real de paradas devolvido pela API — mesmo nas rotas non-stop, se a
tarifa mais barata encontrada naquela semana for uma conexão, o alerta
avisa isso claramente. Nas rotas sem non-stop confirmado, paradas são
informadas de forma neutra (é esperado ter conexão).

## Custo estimado
- **Travelpayouts**: grátis, sem limite relevante para este uso.
- **SerpAPI**: plano grátis = 100 buscas/mês.
- **Teto de segurança**: o script tem um limite rígido de
  `serpapi_monthly_cap` (80/mês, no `config.json`) e **para sozinho** de
  chamar o SerpAPI assim que bate esse número — nunca deixa passar do
  plano grátis, mesmo que o uso real suba mais que o esperado. A cadência
  por prioridade (item 2 acima) existe justamente pra reduzir a chance de
  bater nesse teto com 28 rotas.

## Menções editoriais (Melhores Destinos)
Além das 3 fontes de preço, o script também escaneia a **API REST pública**
do blog [melhoresdestinos.com.br](https://www.melhoresdestinos.com.br)
(`/wp-json/wp/v2/promocao` — não bloqueada pelo robots.txt, grátis, sem
autenticação) atrás de posts recentes cujo título cite um dos seus
destinos (campo `keywords` em cada rota do `config.json`). Cada post
individual do tipo "promocao" já é uma oferta específica, geralmente com
preço no título (ex: *"a partir de R$ 3.548"*).

Isso **não é uma fonte de preço confiável** para a detecção de queda —
são preços "a partir de" para datas não especificadas, não a janela exata
que você configurou. Por isso entram como **menções separadas**
(`editorial_mentions` no JSON), nunca misturadas com os `deals` reais, e
sempre aparecem em **destaque visual próprio** no alerta (Discord,
Telegram e e-mail): um cabeçalho com divisor
(`━━━ MELHORES DESTINOS (fonte externa) ━━━`) e cada item marcado com o
rótulo `🔶 [MELHORES DESTINOS]`, pra você nunca confundir com um preço
confirmado pela nossa busca. Só posts novos desde a última checagem são
reportados (controlado por `_melhoresdestinos.last_seen_id` no
`price_history.json`).

## Buscadores avaliados e não integrados
A pedido do usuário, avaliamos Kayak, Skyscanner, Skiplagged e "FlightFare"
como fontes adicionais. Nenhum tem API pública gratuita legítima: Kayak e
Skyscanner só liberam API para parceiros de negócio aprovados; Skiplagged
proíbe expressamente engenharia reversa da API não-documentada nos termos
de uso. SerpAPI + Travelpayouts continuam sendo as únicas fontes usadas.

## Arquivos
- `config.json` — rotas, parâmetros de busca e as API keys/tokens (SerpAPI,
  Travelpayouts, Discord, Telegram). **Não compartilhe este arquivo** —
  contém suas credenciais.
- `check_flights.py` — script que faz a checagem e o disparo pro
  Discord/Telegram (sem dependências além do Python padrão).
- `price_history.json` — histórico de preços por rota + contador de uso
  mensal do SerpAPI, atualizado a cada execução.
- `run_log.txt` — log simples de cada execução, para depuração.

## Milhas / pontos (seats.aero)
Não está automatizado aqui — decidimos usar os **alertas nativos e grátis
do próprio site do seats.aero** (ilimitados) em vez de gastar a API paga.
Crie os alertas de disponibilidade por milhas direto em
https://seats.aero para cada rota que quiser acompanhar.

## Editar rotas, período, sensibilidade do alerta
Edite `config.json`:
- `routes` — cada rota aceita `id`, `label`, `arrival` (lista de códigos
  IATA), `origin` (opcional, padrão é o `origin` global — usado pelas
  rotas de Fernando de Noronha), `carriers`, `single_carrier`,
  `verified_nonstop` (controla a cadência de checagem e o tom do alerta
  de paradas) e `note` (opcional).
- `window_months`, `stay_min_days`/`max_days` — período e duração da
  viagem.
- `drop_threshold_pct`, `renotify_cooldown_days` — sensibilidade do
  alerta.

Depois de editar, rode `python3 check_flights.py` manualmente para testar
(gasta cota do SerpAPI se alguma rota cair no fallback), ou espere a
próxima segunda-feira.

## Gerenciar a tarefa agendada
Na sidebar do Claude Code, seção "Scheduled" → tarefa "Monitor de preços de
voos (28 rotas)". De lá dá pra pausar, rodar agora, mudar horário/frequência
ou excluir.
