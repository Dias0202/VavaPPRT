# X9: o bot dedo-duro do Discord

> **Status: APROVADO** (design fechado em 2026-10-12, sessão de *grill*).
> **X9 v2 implementado em 2026-10-15** (velocidade, insights e criatividade;
> diagnóstico e metas em [PLANO_X9_V2.md](PLANO_X9_V2.md)).
> Este documento é o SDD do bot e a fonte da verdade do contrato da `botapi`.

O X9 é um bot conversacional de Discord que **dedura** as partidas do grupo
(quem carregou e quem afundou), **tira dúvidas de desempenho** e **narra rounds
específicos**. Fala e escuta em **pt-br**, por texto e por voz, e roda inteiro em
**free tier**.

---

## 1. Decisões

| Tema | Decisão | Por quê |
|---|---|---|
| Modalidade | Texto **e** voz | Requisito: "falar e escutar". |
| Onde roda | **PC de alguém do grupo** (processo sempre ligado enquanto o PC estiver) | O Render free dorme e tem 512 MB, sem condições para voz. O PC tem CPU/GPU para o Whisper. |
| Acesso a dados | O bot **importa a `botapi` e lê o Postgres (Supabase) direto** | Sem *cold start* do Render, sem hop HTTP. |
| "A API" | `backend/app/botapi.py`: catálogo de **tools** tipadas e compactas, pensadas para LLM | Um contrato só, consumido pelo LLM e por HTTP. |
| Espelho HTTP | `/bot/v1/tools` no FastAPI, com **token de serviço** | Outros clientes no futuro; dá para testar pelo `/docs`. |
| LLM | **Gemini Flash** e **Groq** (`gpt-oss-120b`/`20b`) com **rotas por tarefa**: texto → Gemini; voz → Groq (latência menor); fofoca → Gemini com um pouco de raciocínio. Castigo de cota **compartilhado** entre as rotas | Bom em pt-br, tools nativas, e a cota estourada de um não derruba o bot. |
| Contexto | **Briefing** do grupo no prompt (últimas 5 partidas, elo/RR, noite de hoje, fatos da última, lore, quem está on) | As perguntas comuns saem em 1 chamada, sem tool (docs/PLANO_X9_V2.md §2.2). |
| Streaming | Texto: a mensagem se escreve enquanto o LLM gera (edição ≤ 1/s). Voz: frase a frase | A 1ª frase chega em ~1 s. |
| Insights | **Motor de fatos** (`backend/app/storylines.py`): ganchos com nota de surpresa — contra a própria média, recordes, RR/elo, sequências, noite, mapa, lobby | A zoeira ganha contexto e nunca inventa número. |
| STT | **faster-whisper local** (`small`, pt) | Grátis, offline, bom em pt-br. |
| TTS | **edge-tts** (`pt-BR-AntonioNeural`), com **Piper local** (`pt_BR-faber-medium`) de reserva | O edge-tts é grátis e neural, mas é um endpoint não oficial que às vezes responde 403 (aconteceu durante a implementação). Quando falha, fica 30 min de castigo e o Piper fala. |
| Voz no Discord | `discord.py[voice]` ≥ 2.7.1 (DAVE via `davey`) + fork `idoldange/discord-ext-voice-recv` | Desde mar/2026 o Discord só aceita voz com E2EE (DAVE). O pacote upstream de *voice receive* não decifra DAVE; o fork decifra. |
| Ativação na call | Palavra **"X9"** no começo da frase, com casamento aproximado ("xis nove", "x-9", "xisnove"…) | Natural, sem comando. |
| Entrada na call | Só com **`/x9 entrar`** | Não é invasivo e poupa CPU. |
| Voz proativa | Já na call, **fala um resumo curto** (15–20 s) de cada partida nova | Dedo-duro presencial. |
| Texto | **`@X9` em linguagem livre** + **`/x9 …`** para o determinístico. Respostas **em thread** | Não polui o canal. |
| Dedo-duro | **Os dois**: o poller posta um **embed factual** via *webhook* (sem LLM); o bot, se online, **responde na thread** da mensagem com a zoeira | Funciona com o PC desligado; a fofoca vem quando dá. |
| Fofoca | **1 chamada** de LLM com os fatos prontos, num **formato sorteado** (plantão, narração de futebol, boletim de ocorrência, laudo…; nunca os 3 últimos), com as aberturas recentes vetadas e um bloco `VOZ:` que vira a fala da call | Rápida, variada e sem 2ª cadeia para a voz. |
| Tom | **Sem filtro** (`HARASSMENT: BLOCK_NONE`), mirando **o desempenho no jogo**. `/x9 modo` troca por canal: `zoeira`, `coach` (sério) ou `narrador` | Grupo de amigos. Discurso de ódio continua bloqueado pelo Gemini e proibido no prompt. |
| Proativo | Pré-fila quando 2+ abrem o jogo, resenha no fim da noite, Oscar do X9 no domingo, aviso de quem subiu/caiu de elo, votação de MVP, apostas | O X9 puxa assunto sem ninguém perguntar (cada um com liga/desliga). |
| Identidade | **Admin mapeia** `@usuário` ↔ Riot ID (`/x9 vincular`, exige "Gerenciar Servidor") | Controle central; resolve "eu" e quem fala na call. |
| Rounds | Narrar um round, achar rounds por evento, momentos-chave e "por que perdemos" | Requisito: "falar de rounds em específico". |
| Partida sem detalhe | **Busca sob demanda** na HenrikDev (1 req) pelo `ingest` existente | O usuário recebe a resposta, e não um "não sei". |
| Memória | **Janela curta por canal/thread**: ~10 trocas, TTL de 15 min e **partida em foco**. **Lore** longa no banco: apelidos da semana e momentos marcantes (30 dias) | Resolve "e o round seguinte?" e dá callback ("igual à Bind de terça"). |

---

## 2. Arquitetura

```
                     ┌──────────────────────── Supabase (Postgres) ────────────────────────┐
                     │ matches · match_players · kills · player_round_stats · …            │
                     │ discord_links (novo) · bot_announcements (novo)                     │
                     └───────▲───────────────────────▲──────────────────────▲──────────────┘
                             │ grava                 │ lê / grava           │ lê
   GitHub Actions ───────────┤                       │                      │
   poller (5 min)            │                       │                      │
     ingest_all()            │              ┌────────┴─────────┐   ┌────────┴────────┐
     announce.run() ──► webhook do Discord   │  bot X9 (PC)     │   │ FastAPI (Render)│
                        (embed placar)       │  import botapi   │   │ /bot/v1/tools   │
                             │               │  Gemini ⇄ Groq   │   │ (token serviço) │
                             ▼               │  Whisper · TTS   │   └─────────────────┘
                     #canal do Discord ◄─────┤  discord.py      │
                      └─ thread: zoeira      └──────────────────┘
                      🔊 call: resumo falado
```

### 2.1 Fluxo do dedo-duro

1. O **poller** grava as partidas novas de todos e **anuncia logo em seguida**
   (antes do MMR e do deep backfill). Depois do MMR, **edita a mensagem do
   webhook** com o RR e quem subiu ou caiu de elo.
2. Para cada partida nova do grupo (dentro da janela de anúncio), faz um
   **claim** atômico em `bot_announcements` (`INSERT … ON CONFLICT DO NOTHING`).
   Quem ganha o claim posta. Isso evita post duplo entre poller e bot.
3. Posta o **embed factual** no webhook (`?wait=true`) e grava `channel_id`,
   `message_id`, `ended_raw` (fim da partida) e `posted_raw` (o cronômetro do
   `/x9 status`).
4. O **bot** vê a mensagem do webhook chegar pelo gateway e dispara a fofoca
   **na hora** (`Announcer.kick`); o tick de 2 min virou só rede de segurança.
   A fofoca é **reservada** no banco (`claim_gossip`): ao-vivo, tick, kick e até
   uma 2ª instância nunca postam a mesma duas vezes.
5. A fofoca é **uma chamada** de LLM com o material pronto
   (`botapi.material_fofoca`: placar, linha de cada amigo e os melhores fatos do
   motor de fatos), num formato sorteado. Vai para a **thread** do placar; o bloco
   `VOZ:` é falado na call, se o X9 estiver numa.
6. Depois: reações no placar (🎯 ace, 🧊 clutch, 💀 último do lobby, 🔥 recorde…),
   apostas liquidadas, botões de **votação de MVP** e a **lore** (momentos e
   apelidos) atualizada.
7. Falhou ao postar (thread arquivada, rede): conta a tentativa e **guarda o
   texto gerado**, então a próxima tentativa não paga o LLM de novo. Depois de 3,
   o X9 posta solto no canal e desiste. Sem LLM (cota): a fofoca volta para a fila
   sem contar tentativa.
8. Se o PC ficou desligado, o bot processa o atraso quando volta (anúncios das
   últimas 48 h; com mais de 3 pendentes, junta tudo num **resumo único** "enquanto
   eu tava off…").
9. Se o webhook não estiver configurado, o próprio bot faz o claim das partidas
   com mais de 80 min sem dono (folga para o poller) e posta o embed.

Primeira execução: a janela começa em `agora − 6 h` (`app_config.announce_since`),
para não despejar o histórico inteiro no canal.

### 2.1b Tempo (quase) real — `x9/live.py`

Nem a Riot nem a HenrikDev expõem a partida **ao vivo**: ela só existe depois
que acaba. O X9 encurta a espera de até 5 min (poller) para **segundos depois de
a HenrikDev publicá-la** (ela demora de 1 a 3 min):

| Situação | O que o bot faz |
|---|---|
| Jogador vinculado com **VALORANT** na atividade do Discord | Consulta a HenrikDev para ele a cada **60 s** |
| A atividade **mudou** (saiu da partida, voltou ao menu) ou o jogo **fechou** | Rajada de checagens em +30 s, 1, 1,5, 2, 3 e 5 min |
| Ninguém jogando / atividade oculta | Varredura de todos a cada 5 min |
| Mesma **party** no Discord | Checa um e o resto vai junto (o stack conta como um só) |
| Achou partida nova | Grava, **posta o placar na hora** (claim) com a fofoca gerada **em paralelo**, tira **todos os amigos da partida** da rajada, e só então busca o RR em segundo plano e **edita o placar** com ele |

A checagem pede só as 2 partidas mais novas (`LIVE_FETCH_SIZE`), não as 5
completas, e um 429 não trava o loop: desiste e tenta no ciclo seguinte.
Orçamento: no máximo 1 consulta a cada 5 s (`LIVE_MIN_GAP_S`), dos 30 req/min
que a chave permite, dividida com o poller. O poller do GitHub continua rodando
(é ele que anuncia com o PC desligado) e pula quem o bot checou há menos de
5,5 min (`POLL_SKIP_RECENT_S`): com o bot ligado, ele não divide a cota.

Precisa, no Developer Portal, de **Presence Intent** e **Server Members
Intent**. A presença só aparece para quem compartilha a atividade no Discord
(Configurações → Privacidade da atividade) e está vinculado; para os outros
vale a varredura de 5 min. A tool `quem_ta_jogando` responde "quem tá on agora?",
e quem está on entra no contexto do LLM.

### 2.1c Momentos proativos — `x9/moments.py`

| Momento | Quando | O que sai no canal do placar |
|---|---|---|
| Pré-fila | 2+ do grupo abrem o VALORANT (espera ~90 s para o resto do stack chegar; 1×/4 h por grupo) | Preleção: mapas para aceitar/evitar com o número, e a escalação (sem LLM, sai a versão só com números) |
| Resenha da noite | Todo mundo fechou o jogo há 20 min e houve 2+ partidas na sessão | Saldo, MVP, bagre, quem tiltou, RR de cada um |
| Oscar do X9 | Domingo a partir das 22 h (hora do PC), 1×/semana (marcado no banco) | Categorias da semana com o vencedor e a prova |
| Mudou de elo | O RR chegou e alguém subiu/caiu de tier | Aviso na thread do placar (e falado na call) |

### 2.2 Fluxo de uma pergunta

```
texto (@X9 / thread / /x9 pergunta)  ─┐
voz: PCM → Whisper → "X9, …" ─────────┴─► Brain.answer(canal, quem_fala, texto)
     persona do modo + BRIEFING do grupo (cache) + memória + partida em foco
     ──► LLM da rota (texto: Gemini · voz: Groq), em streaming
     só se o briefing não bastar: catálogo enxuto de 8 tools (≤ 6 passos)
     texto: a mensagem se escreve ao vivo (edição ≤ 1/s)
     voz: cada frase pronta → TTS → toca, enquanto a próxima é sintetizada
```

Na voz, o fim da fala é detectado após 0,55 s de silêncio. Com GPU, o começo
da fala (1,2 s) é transcrito antes: se for "X9…", o aviso ("Pera, puxando a
ficha…") toca assim que a pessoa para de falar e, se o X9 estiver falando, ele
cala (barge-in). Se a cota do LLM pedir espera, o X9 avisa no canal em vez de
ficar mudo.

## 3. Modelo de dados (novo)

```sql
-- Discord ↔ jogador do roster (mapeado pelo admin)
discord_links (
  discord_id  TEXT PRIMARY KEY,
  puuid       TEXT NOT NULL,
  linked_by   TEXT,            -- discord_id de quem vinculou
  created_raw BIGINT
)

-- Uma linha por partida anunciada (claim + estado da fofoca)
bot_announcements (
  match_id    TEXT PRIMARY KEY,
  claimed_by  TEXT,            -- 'poller' | 'bot'
  claimed_raw BIGINT,
  channel_id  TEXT, message_id TEXT, thread_id TEXT,
  gossip_raw  BIGINT,          -- quando a zoeira saiu (NULL = pendente)
  voice_raw   BIGINT           -- quando foi falada na call
)
```

X9 v2 acrescenta:

```sql
-- bot_announcements ganha: ended_raw, posted_raw (cronômetro), gossip_text,
-- voice_text, gossip_format, gossip_tries (retentativa sem pagar o LLM de novo).
-- gossip_raw < 0 = fofoca em andamento desde |gossip_raw| (claim; vence em 10 min).

bot_lore (id, lore_key UNIQUE, puuid, kind, text, created_raw, expires_raw)
  -- apelido:<chave> (7 dias, muda de dono) · momento:<partida>:<tipo>:<puuid> (30 dias)
bot_bets (id, discord_id, pick, created_raw, match_id, hit, settled_raw)
  -- /x9 aposta; liquidada pela 1ª partida do grupo que começou depois da aposta
bot_mvp_votes (match_id, voter, puuid, voted_raw, PRIMARY KEY (match_id, voter))
app_config: x9_mode:<canal> (modo do X9) · x9_oscar_week (último Oscar)
```

O opt-out (`purge_puuid`) também apaga `discord_links`, a lore e os votos da pessoa.

---

## 4. Contrato da `botapi` (tools)

Regras gerais:

- **Jogador** (`jogador`): apelido do roster, `Nome`, `Nome#TAG` ou menção
  `<@id_discord>`. Casamento aproximado. Ambiguidade vira erro com as opções.
- **Partida** (`partida`): `"ultima"` (padrão), `"penultima"`, um número `N`
  (N-ésima mais recente, 1 = última) ou o `match_id`. Com `jogador`, conta só as
  partidas dele.
- **Round** (`round`): **1-based**, como o jogador fala ("round 13").
- **Privacidade**: amigos aparecem pelo apelido; **quem não é do grupo aparece
  só pelo agente** ("Jett (inimigo)", "Sova (aliado)"). Nunca Riot ID, nunca
  puuid (política da Riot contra *scouting*).
- Todo erro volta como `{"erro": "mensagem pt-br"}`. A tool nunca lança exceção
  para o LLM.

| Tool | Parâmetros | Devolve |
|---|---|---|
| `listar_jogadores` | — | roster: apelido, Riot ID, se tem Discord vinculado |
| `partidas_recentes` | `jogador?`, `limite=5` | lista: match_id, mapa, data, placar, resultado, amigos presentes (agente, KDA, ACS) |
| `resumo_partida` | `partida?`, `jogador?` | placar dos 10 (amigos por nome), placar de rounds, MVP/pior do grupo, sequências, detalhe disponível? |
| `narrar_round` | `round`, `partida?`, `jogador?` | vencedor, como acabou, economia dos dois times, kills em ordem com tempo, trade, quem abriu, plant/defuse, sobreviventes |
| `buscar_rounds` | `evento`, `partida?`, `jogador?` | rounds que batem: `clutch`, `ace`, `multikill`, `first_blood`, `first_death`, `eco_ganho`, `full_perdido`, `pistol`, `perdidos`, `vencidos`, `flawless`, `vantagem_perdida` |
| `momentos_chave` | `partida?`, `jogador?` | clutches, aces/4k, ecos ganhos, full buys perdidos, viradas, maior sequência, pistols |
| `analisar_round` | `round`, `partida?`, `jogador?` | evidências estruturadas (abertura, mortes sem trade, vantagem numérica perdida, plant, economia, sobreviventes) para o LLM dar o veredito |
| `desempenho_jogador` | `jogador` | visão geral (WR, K/D, ACS, ADR, HS%, KAST, entrada, clutch, trade, economia, lados) + pontos fortes/fracos |
| `tendencia_jogador` | `jogador`, `janela=10` | últimas N vs N anteriores |
| `mapas_e_agentes` | `jogador` | WR/KD por mapa e por agente, com selo de confiança |
| `comparar_jogadores` | `jogador_a`, `jogador_b` | dupla: juntos vs separados, quem carrega, quem vinga quem |
| `ranking_grupo` | — | leaderboard do roster |
| `resumo_semanal` | — | o que mudou nos últimos 7 dias |
| `fatos_partida` | `partida?`, `jogador?` | ganchos com nota de surpresa: vs a própria média, recordes, RR/elo, sequências, noite, mapa, lobby, estreia de agente |
| `analisar_partida` | `partida?`, `jogador?` | por que ganhou/perdeu: aberturas, trades, economia, pós-plant/retake, lados, pistols, ordenados por impacto |
| `elo_e_rr` | `jogador?` | elo, RR, pico, últimas variações, saldo de hoje e da semana (um ou todos) |
| `noite_de_hoje` | `jogador?` | a sessão mais recente: resultados, RR, MVP, bagre, tilt |
| `pre_fila` | `jogadores` | mapas para filar/evitar (stack exato > duplas > individual) e escalação |
| `sinergia_grupo` | — | melhores e piores duplas (com piso de amostra) |
| `armas_jogador` | `jogador` | kills e HS% por arma |
| `contra_o_lobby` | `jogador` | posição média no lobby/time e contra a média do elo |
| `metas_jogador` | `jogador` | metas de treino e progresso |
| `recordes` | `jogador?` | melhor/pior ACS, mais kills, maiores sequências |

**Catálogo do LLM (`llm_catalog`)**: o bot não manda as 23 tools ao LLM, e sim 8
agrupadas por `aspecto` (`partidas_recentes`, `partida`, `round`, `buscar_rounds`,
`jogador`, `grupo`, `comparar_jogadores`, `pre_fila`) — menos da metade dos tokens
por passo. `call_tool` aceita os dois catálogos; o espelho HTTP segue o completo.

**Cache**: a `botapi` guarda em memória o raw das partidas (LRU), roster e
vínculos (60 s) e o resto até a próxima partida nova (`bump_version`) ou 45 s/5 min.
O bot chama `bump_version` quando grava ou vê uma partida nova.

Partida **sem detalhe** (só resumo): `narrar_round`, `buscar_rounds`,
`momentos_chave` e `analisar_round` chamam `garantir_detalhe(match_id)`, que
re-hidrata via `/v4/match` (1 requisição, mesmo *throttle* do poller). Em 429,
devolvem `{"erro": "…tenta de novo em 1 min"}`.

### 4.1 Espelho HTTP

| Método | Rota | Corpo | Resposta |
|---|---|---|---|
| GET | `/bot/v1/tools` | — | catálogo: `[{name, description, parameters}]` (JSON Schema) |
| POST | `/bot/v1/tools/{name}` | `{"args": {...}}` | o resultado da tool |

Auth: `Authorization: Bearer $BOT_API_TOKEN`. Sem a env, a rota responde **503**.
Token errado: **401**. Tool inexistente: **404**.

---

## 5. Componentes do bot (`bot/x9/`)

| Módulo | Papel | Depende de discord? |
|---|---|---|
| `config.py` | variáveis de ambiente | não |
| `llm.py` | `GeminiProvider`, `GroqProvider` (com `chat_stream`), `FallbackProvider` com saúde compartilhada entre rotas, loop de tools com cronômetro | não |
| `brain.py` | personas (modos), briefing, memória, partida em foco, `answer()` em streaming, `gossip()` em 1 chamada com formato sorteado, resenha, pré-fila, Oscar | não |
| `wake.py` | detecção da palavra "X9" na transcrição | não |
| `audio.py` | PCM 48 kHz estéreo → 16 kHz mono; MP3 → PCM 48 kHz (PyAV, sem ffmpeg) | não |
| `stt.py` / `tts.py` | faster-whisper (CUDA → CPU automático) / `Speaker`: edge-tts → Piper | não |
| `announcer.py` | claim do placar e da fofoca, fofoca em paralelo, kick do webhook, retentativa, reações, apostas, MVP, lore, RR no placar | interface |
| `live.py` | presença → agenda de checagens na HenrikDev (stack e party); anúncio na hora; RR depois; `quem_ta_jogando` | não |
| `moments.py` | pré-fila, fim da noite e Oscar do X9 | não |
| `streaming.py` | mensagem que se escreve ao vivo (edições com freio) | não |
| `telemetry.py` | p50/p95 de respostas, voz e partidas para o `/x9 status` | não |
| `voice.py` | sink (com amostra para wake antecipado), fala frase a frase, avisos, barge-in | sim |
| `discord_bot.py` | cliente, `/x9 …`, menções, threads, botões de MVP, posts proativos | sim |

### Comandos

| Comando | Quem | Faz |
|---|---|---|
| `@X9 <pergunta>` | todos | responde numa thread; dentro da thread não precisa mencionar |
| `/x9 pergunta texto:` | todos | igual à menção |
| `/x9 ultima [jogador]` | todos | embed da última partida (determinístico, sem LLM) |
| `/x9 hoje` · `/x9 elo` · `/x9 ranking` | todos | a noite, o elo/RR de todos e o ranking (sem LLM, instantâneos) |
| `/x9 fila [jogadores]` | todos | pré-fila: mapas e escalação (padrão: quem está on + você) |
| `/x9 modo modo:` | todos | personalidade do X9 neste canal: zoeira, coach ou narrador |
| `/x9 aposta resultado:` · `/x9 apostas` | todos | aposta na próxima partida do grupo · placar de acertos |
| `/x9 lore [apagar:]` | todos (apagar: Gerenciar Servidor) | apelidos e momentos que o X9 lembra |
| `/x9 status` | todos (resposta só para você) | p50/p95 das respostas, da voz e das partidas, saúde dos LLMs, ao-vivo, poller, cache |
| `/x9 entrar` · `/x9 sair` | todos | entra/sai da sua call |
| `/x9 vincular membro: riot_id:` | Gerenciar Servidor | liga `@membro` ao `Nome#TAG` do roster |
| `/x9 desvincular membro:` | Gerenciar Servidor | remove o vínculo |
| `/x9 vinculos` | todos | lista os vínculos |

---

## 6. Configuração

**Poller (GitHub Actions secrets)**: `DISCORD_WEBHOOK_URL` (opcional: sem ele, o
poller não anuncia) e `SITE_URL` (opcional, link no embed).

**API (Render)**: `BOT_API_TOKEN` (opcional: sem ele, `/bot/v1` responde 503).

**Bot (`bot/.env`)**:

```
DISCORD_TOKEN=            # token do bot (Developer Portal)
ANNOUNCE_CHANNEL_ID=      # canal do placar (mesmo do webhook)
DATABASE_URL=             # mesmo session pooler do Supabase
HENRIKDEV_API_KEY=        # para a busca sob demanda
GEMINI_API_KEY=           # aistudio.google.com
GEMINI_MODEL=gemini-2.5-flash
GROQ_API_KEY=             # console.groq.com (fallback)
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_MODEL=openai/gpt-oss-20b  # cota por minuto separada; vazio desliga
LLM_ORDER_TEXT=gemini,groq,groq-small     # rotas por tarefa
LLM_ORDER_VOICE=groq,groq-small,gemini
LLM_ORDER_GOSSIP=gemini,groq,groq-small
GEMINI_GOSSIP_THINKING=512                # "pensar" só na fofoca (0 desliga)
WHISPER_MODEL=small       # ou large-v3-turbo na GPU (com WHISPER_COMPUTE=int8_float16)
WHISPER_DEVICE=auto       # auto | cpu | cuda (cai para CPU sozinho se faltar DLL do CUDA)
WHISPER_COMPUTE=          # vazio = float16 na GPU / int8 na CPU
VOICE_SILENCE_S=0.55      # silêncio que encerra a fala
VOICE_EARLY_WAKE=auto     # wake antecipado + barge-in (auto = só com GPU)
TTS_ENGINE=auto           # auto (edge → piper) | edge | piper
TTS_VOICE=pt-BR-AntonioNeural
PIPER_VOICE=pt_BR-faber-medium   # baixada sozinha em bot/models/piper na 1ª vez
LIVE_PLAYING_EVERY_S=60   # quem está jogando
LIVE_MIN_GAP_S=5          # teto de consultas (com chave Advanced da HenrikDev dá para baixar)
LIVE_FETCH_SIZE=2         # partidas por checagem (0 = padrão da API)
STREAM_REPLIES=1          # resposta escrita ao vivo
GOSSIP_REACTIONS=1 · MVP_VOTE=1 · PROACTIVE_RANK=1
PROACTIVE_PREFILA=1 · PROACTIVE_NIGHT=1 · PROACTIVE_OSCAR=1
```

**Poller (GitHub Actions)**: `POLL_SKIP_RECENT_S` (variável do repositório,
padrão 330 no workflow) pula quem o ao-vivo do bot checou há pouco.

No Developer Portal, ligar o intent **Message Content**. No convite, usar os
escopos `bot applications.commands` com as permissões: ver canais, enviar
mensagens, criar threads públicas, enviar mensagens em threads, ler histórico,
conectar e falar.

---

## 7. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| A cota da HenrikDev (30 req/min) é dividida entre poller e bot | A busca sob demanda usa o mesmo `_throttle` e desiste no 429 com mensagem clara. |
| Privacidade de adversários | Labels por agente; teste automatizado garante que nenhum nome de não-membro sai das tools. |
| Rastreados que nunca consentiram em ser zoados | O roster respeita `consent`; quem tem `consent=0` some das tools. O grupo deve combinar isso. |
| O tier grátis do Gemini pode usar os dados para treino | Só sobem estatísticas de jogo e apelidos. |
| edge-tts responde 403 (endpoint não oficial) | Desde out/2026 o host antigo recusa a síntese: o `x9.tts` aponta o edge-tts para `api.msedgeservices.com` (correção do PR #491, ainda não publicada no 7.2.8; vira no-op quando sair). Se ainda falhar, cai no Piper e tenta o edge de novo após 30 min. |
| Whisper contínuo pesa no PC | Modelo `small`, transcrição só de falas de 0,4 a 15 s, com fila serializada. |
| A recepção de voz usa um fork sem garantia de estabilidade | Commit fixado no `requirements.txt`; texto e anúncios funcionam mesmo se a voz quebrar. |
| PC desligado | O webhook do poller continua postando o placar; a fofoca sai quando o bot voltar. |
| Os limites do free tier do Gemini mudam sem aviso | Fallback para o Groq; modelo configurável por env; rotas configuráveis. |
| Proatividade vira spam | Cada momento tem liga/desliga; pré-fila 1×/4 h por grupo, resenha 1× por sessão, Oscar 1×/semana. |
| Apelidos e lore passam do ponto | Mesma regra do prompt (só desempenho), validade de 7/30 dias e `/x9 lore apagar`. |
| Cache servindo dado velho | `bump_version` a cada partida nova vista pelo bot; prazos de 45 s a 5 min para o que entra por fora. |

---

## 8. Subindo o X9 (passo a passo)

1. **Discord Developer Portal** → *New Application* "X9" → aba **Bot**:
   *Reset Token* (vai em `DISCORD_TOKEN`) e ligar **Message Content Intent**.
2. **Convite** (aba *OAuth2 → URL Generator*): escopos `bot` e
   `applications.commands`; permissões *View Channels, Send Messages, Create
   Public Threads, Send Messages in Threads, Read Message History, Embed Links,
   Connect, Speak*. Abra a URL e adicione ao servidor do grupo.
3. **Webhook do placar**: no canal escolhido → *Editar canal → Integrações →
   Webhooks → Novo*. Copie a URL para o secret `DISCORD_WEBHOOK_URL` do
   repositório (GitHub → Settings → Secrets → Actions). O ID desse canal vai em
   `ANNOUNCE_CHANNEL_ID`.
4. **LLM**: chave em [aistudio.google.com](https://aistudio.google.com/apikey)
   (`GEMINI_API_KEY`) e, de reserva, em [console.groq.com](https://console.groq.com/keys)
   (`GROQ_API_KEY`).
5. **No PC**: `cd bot` → `.\run.ps1` (cria a venv com Python 3.11 e o `.env`),
   preencha o `bot\.env`, rode `.\run.ps1 check` e depois `.\run.ps1`.
   Para deixar ligado sozinho: `.\install-autostart.ps1` (tarefa agendada
   "X9 Discord Bot": sobe oculto no logon e reinicia se cair; log em
   `bot\logs\x9.log`; `-Remove` desinstala).
6. No Discord: `/x9 vincular @fulano Nome#TAG` para cada um do grupo.
7. Teste: `@X9 quem carregou na última?`, `/x9 ultima`, `/x9 entrar` e fale
   "X9, conta o round 13".

A primeira entrada na call baixa o Whisper `small` (~460 MB) e a voz do Piper
(~63 MB). Com GPU NVIDIA, o `run.ps1` instala `requirements-gpu.txt` (cuBLAS 12
e cuDNN 9 como wheels dentro da venv) e o Whisper roda na CUDA (~1 s por frase
numa GTX 1650 Super). Sem GPU, roda na CPU (~2 s por frase).

No Developer Portal ficam ligados **Message Content** e **Server Members**
(este para listar os membros na hora de vincular) e o **Public Bot desligado**
(ligado, qualquer servidor poderia adicionar o X9 e consultar o grupo; para
desligar, o link de instalação precisa estar em "Nenhum").

---

## 9. Outros servidores e novos vínculos

**Vincular alguém** (precisa de *Gerenciar Servidor* no Discord):

1. A pessoa tem que estar no grupo do **site** (cadastro próprio ou *Adicionar
   jogador* no roster). O X9 só conhece quem está no roster.
2. No Discord: `/x9 vincular membro:@fulano riot_id:Nome#TAG` (aceita também o
   apelido do site). `/x9 vinculos` lista e `/x9 desvincular` desfaz.
3. A pessoa precisa estar no mesmo servidor em que o comando é usado (é de lá
   que sai o @). Sem vínculo tudo funciona, menos o "eu".

**Colocar o X9 em outro servidor**: o bot é **privado** (Public Bot desligado),
então só a conta dona do app adiciona. Com essa conta, abra

`https://discord.com/oauth2/authorize?client_id=1559205573633908819&scope=bot%20applications.commands&permissions=309240876032`

escolha o servidor (você precisa ter *Gerenciar Servidor* nele) e autorize.
Os `/x9` são globais e aparecem sozinhos. Lembre que **é o mesmo grupo**: o X9
responde com os dados do roster do site em qualquer servidor em que estiver.
Para um grupo diferente seria outra instância (outro banco, outro app).

O **placar automático** sai num canal só: o do webhook (`DISCORD_WEBHOOK_URL`
no GitHub) e o `ANNOUNCE_CHANNEL_ID` do bot. Para trocar de canal, crie o webhook
no canal novo e atualize os dois.
