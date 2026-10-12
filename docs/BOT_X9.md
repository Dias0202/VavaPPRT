# X9: o bot dedo-duro do Discord

> **Status: APROVADO** (design fechado em 2026-10-12, sessão de *grill*).
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
| LLM | **Gemini Flash** (free tier, function calling). Se cair, **Groq** (Llama) com o mesmo schema de tools | Bom em pt-br e com tools nativas. Cota estourada não derruba o bot. |
| STT | **faster-whisper local** (`small`, pt) | Grátis, offline, bom em pt-br. |
| TTS | **edge-tts** (`pt-BR-AntonioNeural`), com **Piper local** (`pt_BR-faber-medium`) de reserva | O edge-tts é grátis e neural, mas é um endpoint não oficial que às vezes responde 403 (aconteceu durante a implementação). Quando falha, fica 30 min de castigo e o Piper fala. |
| Voz no Discord | `discord.py[voice]` ≥ 2.7.1 (DAVE via `davey`) + fork `idoldange/discord-ext-voice-recv` | Desde mar/2026 o Discord só aceita voz com E2EE (DAVE). O pacote upstream de *voice receive* não decifra DAVE; o fork decifra. |
| Ativação na call | Palavra **"X9"** no começo da frase, com casamento aproximado ("xis nove", "x-9", "xisnove"…) | Natural, sem comando. |
| Entrada na call | Só com **`/x9 entrar`** | Não é invasivo e poupa CPU. |
| Voz proativa | Já na call, **fala um resumo curto** (15–20 s) de cada partida nova | Dedo-duro presencial. |
| Texto | **`@X9` em linguagem livre** + **`/x9 …`** para o determinístico. Respostas **em thread** | Não polui o canal. |
| Dedo-duro | **Os dois**: o poller posta um **embed factual** via *webhook* (sem LLM); o bot, se online, **responde na thread** da mensagem com a zoeira | Funciona com o PC desligado; a fofoca vem quando dá. |
| Tom | **Sem filtro** (`HARASSMENT: BLOCK_NONE`), mirando **o desempenho no jogo** | Grupo de amigos. Discurso de ódio continua bloqueado pelo Gemini e proibido no prompt. |
| Identidade | **Admin mapeia** `@usuário` ↔ Riot ID (`/x9 vincular`, exige "Gerenciar Servidor") | Controle central; resolve "eu" e quem fala na call. |
| Rounds | Narrar um round, achar rounds por evento, momentos-chave e "por que perdemos" | Requisito: "falar de rounds em específico". |
| Partida sem detalhe | **Busca sob demanda** na HenrikDev (1 req) pelo `ingest` existente | O usuário recebe a resposta, e não um "não sei". |
| Memória | **Janela curta por canal/thread**: ~10 trocas, TTL de 15 min e **partida em foco** | Resolve "e o round seguinte?" sem custo de banco. |

---

## 2. Arquitetura

```
                     ┌──────────────────────── Supabase (Postgres) ────────────────────────┐
                     │ matches · match_players · kills · player_round_stats · …            │
                     │ discord_links (novo) · bot_announcements (novo)                     │
                     └───────▲───────────────────────▲──────────────────────▲──────────────┘
                             │ grava                 │ lê / grava           │ lê
   GitHub Actions ───────────┤                       │                      │
   poller (15 min)           │                       │                      │
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

1. O **poller** termina `ingest_all()` e chama `announce.run()`.
2. Para cada partida nova do grupo (dentro da janela de anúncio), faz um
   **claim** atômico em `bot_announcements` (`INSERT … ON CONFLICT DO NOTHING`).
   Quem ganha o claim posta. Isso evita post duplo entre poller e bot.
3. Posta o **embed factual** no webhook (`?wait=true`) e grava `channel_id` e
   `message_id`.
4. O **bot** (loop de 2 min) busca anúncios com `gossip_raw IS NULL`, abre uma
   **thread** na mensagem e posta a **zoeira** gerada pelo LLM. Se estiver numa
   call, **fala** a versão curta.
5. Se o PC ficou desligado, o bot processa o atraso quando volta (anúncios das
   últimas 48 h; com mais de 3 pendentes, junta tudo num **resumo único** "enquanto
   eu tava off…").
6. Se o webhook não estiver configurado, o próprio bot faz o claim das partidas
   com mais de 20 min sem dono (folga para o poller) e posta o embed.

Primeira execução: a janela começa em `agora − 6 h` (`app_config.announce_since`),
para não despejar o histórico inteiro no canal.

### 2.1b Tempo (quase) real — `x9/live.py`

Nem a Riot nem a HenrikDev expõem a partida **ao vivo**: ela só existe depois
que acaba. O X9 encurta a espera de até 15 min (poller) para **1 a 3 min depois
do fim da partida** (o que a HenrikDev demora para publicá-la):

| Situação | O que o bot faz |
|---|---|
| Jogador vinculado com **VALORANT** na atividade do Discord | Consulta a HenrikDev para ele a cada 2 min |
| A atividade **mudou** (saiu da partida, voltou ao menu) ou o jogo **fechou** | Rajada de checagens em +45 s, 2, 4 e 8 min |
| Ninguém jogando / atividade oculta | Varredura de todos a cada 5 min |
| Achou partida nova | Grava, atualiza RR, **posta o placar na hora** (claim) e a fofoca na thread; fala na call se estiver em uma |

Orçamento: no máximo 1 consulta a cada 5 s (7 jogadores jogando ≈ 3,5 req/min
dos 30 que a chave permite, dividida com o poller). O poller do GitHub continua
rodando: é ele que anuncia quando o PC está desligado.

Precisa, no Developer Portal, de **Presence Intent** e **Server Members
Intent**. A presença só aparece para quem compartilha a atividade no Discord
(Configurações → Privacidade da atividade) e está vinculado; para os outros
vale a varredura de 5 min. A tool `quem_ta_jogando` responde "quem tá on agora?".

Config: `LIVE_ENABLED` (padrão 1), `LIVE_BASE_EVERY_S` (300),
`LIVE_PLAYING_EVERY_S` (120).

### 2.2 Fluxo de uma pergunta

```
texto (@X9 / thread / /x9 pergunta)  ─┐
voz: PCM → Whisper → "X9, …" ─────────┴─► Brain.answer(canal, quem_fala, texto)
     memória do canal + partida em foco + persona ──► LLM (Gemini → Groq)
     LLM chama tools da botapi (≤ 6 iterações) ──► resposta pt-br
     texto: responde na thread  ·  voz: edge-tts (ou Piper) → PCM → toca na call
```

---

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

O opt-out (`purge_puuid`) também apaga `discord_links` da pessoa.

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
| `llm.py` | `LLMProvider`, `GeminiProvider`, `GroqProvider`, `FallbackProvider` (HTTP puro via httpx) e loop de tools | não |
| `brain.py` | persona, memória por canal, partida em foco, `answer()`, `gossip()`, `voice_line()` | não |
| `wake.py` | detecção da palavra "X9" na transcrição | não |
| `audio.py` | PCM 48 kHz estéreo → 16 kHz mono; MP3 → PCM 48 kHz (PyAV, sem ffmpeg) | não |
| `stt.py` / `tts.py` | faster-whisper (CUDA → CPU automático) / `Speaker`: edge-tts → Piper | não |
| `announcer.py` | claim, embed de fallback, fofoca na thread, voz proativa | interface |
| `live.py` | presença → agenda de checagens na HenrikDev; anúncio na hora; `quem_ta_jogando` | não |
| `voice.py` | sink de recepção, segmentação por silêncio, fila de fala | sim |
| `discord_bot.py` | cliente, `/x9 …`, menções, threads | sim |

### Comandos

| Comando | Quem | Faz |
|---|---|---|
| `@X9 <pergunta>` | todos | responde numa thread; dentro da thread não precisa mencionar |
| `/x9 pergunta texto:` | todos | igual à menção |
| `/x9 ultima [jogador]` | todos | embed da última partida (determinístico, sem LLM) |
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
WHISPER_MODEL=small
WHISPER_DEVICE=auto       # auto | cpu | cuda (cai para CPU sozinho se faltar DLL do CUDA)
TTS_ENGINE=auto           # auto (edge → piper) | edge | piper
TTS_VOICE=pt-BR-AntonioNeural
PIPER_VOICE=pt_BR-faber-medium   # baixada sozinha em bot/models/piper na 1ª vez
```

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
| Os limites do free tier do Gemini mudam sem aviso | Fallback para o Groq; modelo configurável por env. |

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
