# SDD — Documento de Design de Software

**Sistema:** VavaPPRT — backend FastAPI + frontend React + ingestão por cron
**Atualizado:** 2026-06

---

## 1. Visão geral da arquitetura

Três processos independentes sobre um banco compartilhado:

```
Frontend (React/Vite, Vercel)
   │ HTTP/JSON + JWT
   ▼
Backend (FastAPI, Render)  ──lê/escreve──►  Postgres (Supabase) / SQLite (dev)
   ▲                                              ▲
   │ serve SÓ do banco                            │ escreve
   └──────────────────────────────────  Poller (GitHub Actions cron)
                                                  │ HTTP
                                                  ▼
                                     HenrikDev API v4 (30 req/min)
                                     Valorant-API.com (assets, no front)
```

**Invariante de design:** o caminho de leitura (frontend/backend) **nunca** chama
a HenrikDev. Só o poller chama, serializado e com throttle. Isso protege o rate
limit (30 req/min) de N usuários simultâneos.

## 2. Componentes do backend (`backend/app/`)

| Módulo | Responsabilidade |
|---|---|
| `config.py` | Carrega env (`DATABASE_URL`, `HENRIKDEV_API_KEY`, `JWT_SECRET`, CORS, TTL). |
| `db.py` | Conexão (Postgres/SQLite), schema (`init_db`), CRUD, queries de analytics, roster. Tudo chaveado por `puuid`. |
| `auth.py` | Hash de senha (bcrypt) + emissão/validação de JWT (python-jose). |
| `valorant_api.py` | Cliente HTTP síncrono da HenrikDev (v4 matches, account, mmr, lifetime) com **throttle global** (~2,1s entre chamadas). |
| `metrics.py` | Motor de derivação sobre uma partida **v4**: clutch, trade, KAST, first blood/death, lado atk/def, buy type, multikills. Funções puras. |
| `analytics.py` | Agregações por jogador (overview/mapas/agentes/armas), grupos (squads/detalhe/duo), leaderboard, insights. Aplica **gating de amostragem**. |
| `ingest.py` | Ingestão v4 (partidas completas) + backfill (lifetime, resumo). Idempotente. |
| `poll.py` | Entrypoint do cron: `init_db` + `ingest_all`; modo `backfill`. |
| `main.py` | App FastAPI, dependência de auth, CORS, rotas. |
| `schemas.py` | Modelos Pydantic de entrada/saída. |

## 3. Modelo de dados

Chave de junção universal: **`puuid`** (estável; nomes mudam e a API às vezes
retorna nome vazio).

```
users(id, username UNIQUE, password_hash, riot_id, tagline, region,
      puuid, consent, is_admin, created_at)                 -- contas de login
tracked_players(id, puuid UNIQUE, riot_id, tagline, region,
      consent, added_by, created_at)                        -- pessoas sem login
matches(match_id PK, map, mode, queue, season, started_at,
      started_raw, rounds, region, raw_gz BYTEA)            -- raw v4 enxuto+gzip
match_players(match_id, puuid, name, tag, team, agent, won, score,
      kills, deaths, assists, head/body/legshots, dmg_made,
      dmg_received, loadout_avg, party_id, tier, tier_name,
      PK(match_id,puuid))                                   -- TODOS os 10
player_match_derived(match_id, puuid, map, agent, won, rounds, kills,
      deaths, assists, score, damage, shots, first_bloods, first_deaths,
      trade_kills, deaths_traded, kast_rounds, mk2..mk5, atk/def/eco/
      force/full/pistol _rounds/_wins, clutches_json, full_data,
      PK(match_id,puuid))                                   -- AMIGOS, métricas profundas
player_round_stats(match_id, round_id, puuid, kills, damage, headshots,
      loadout_value, buy_type, weapon, alive_end, was_traded,
      attacker_side, PK(match_id,round_id,puuid))           -- detalhe por round
kills(match_id, round_id, order_in_round, time_ms, killer_puuid,
      victim_puuid, weapon, is_first, is_trade, vx, vy,
      PK(match_id,round_id,order_in_round))                 -- todos, p/ heatmap
app_config(key PK, value)  ·  cache_telemetry(cache_key PK, ...)
```

**Roster unificado:** `db.get_roster()` une `users` (com puuid) e
`tracked_players`, dedup por puuid (login prevalece). Leaderboard, grupos e
ingestão operam sobre o roster.

### Estratégia de armazenamento (3 camadas)

O limite é o Supabase free (500 MB). Decisão:
1. **Ingestão pelo v4** (mais enxuto que v3).
2. **`raw_gz`**: o JSON v4 com `player_locations` removido (volumoso) e
   comprimido com gzip → **~14 KB/partida** (vs ~400 KB cru). 1000 partidas ≈
   13 MB. Permite **recomputar** métricas novas no futuro sem re-buscar.
3. **Tabelas normalizadas** para query rápida; nomes/ícones resolvidos na leitura
   via Valorant-API.

### Métricas: completas vs resumo (`full_data`)
- Partidas v4 (ingestão) têm rounds+kills → `full_data=1`, métricas profundas reais.
- Backfill (lifetime) é só resumo → `full_data=0`, deep zerado.
- Analytics **separa**: básico (WR/K-D/ACS/mapas/agentes) soma todas; profundo
  (KAST/clutch/entrada/economia/lado) soma só `full_data=1`. Evita diluição.

## 4. Fluxo de ingestão

```
poll.py → ingest_all():
  roster = get_roster()
  para cada jogador:
    v4 = fetch_matches_v4()                 # recentes (~5)
    para cada partida nova e padrão (mode_type Standard):
      process_match():
        - match_players (10 jogadores)
        - metrics.build_player_derived() p/ amigos (clutch/trade/KAST/...)
        - player_round_stats, kills (com is_first/is_trade)
        - raw_gz = gzip(strip(v4))
      db.save_match_bundle()                # idempotente por match_id

poll.py backfill → backfill_all():
  lifetime paginado → save_summary_match() (full_data=0)
```

Adicionar pessoa (`POST /roster`) valida na Henrik, insere em `tracked_players`,
e dispara `ingest_player_now` via **BackgroundTasks** (v4 + backfill).

## 5. Algoritmos-chave (`metrics.py`)

- **Lado atk/def** — inferido: o time que planta na 1ª metade (rounds 0–11) é o
  atacante; 2ª metade inverte; OT alterna.
- **First blood/death** — primeiro kill de cada round (menor `time_in_round_in_ms`).
- **Trade** — um kill é trade se a vítima havia matado um colega do killer até
  `TRADE_WINDOW_MS` (4s) antes. `was_traded` = a morte foi vingada na janela depois.
- **KAST** — round conta se o jogador teve Kill, Assist, Survived ou foi Traded.
- **Clutch (1vX)** — varre os kills do round por tempo mantendo contagem de vivos
  por time; quando o jogador fica último vivo do time com inimigos vivos, registra
  o tamanho X e se venceu. (Denominador = situações de último-vivo; apresentação
  destaca vencidos por tamanho.)
- **Buy type** — por `loadout_value`: <1500 eco, <3900 força, ≥3900 full.

### Grupos (`analytics.py`)
- **squads** — por partida, os membros do roster no mesmo time formam o conjunto
  exato (2–5); agrega por `frozenset`.
- **group_detail** — partidas com o **conjunto EXATO** do grupo no time (índice
  construído com o roster inteiro p/ excluir partidas onde outro membro estava).
  Calcula WR, forma recente, **lift** (WR juntos − WR de cada um sem o grupo),
  mapas, composições, e métricas profundas (soma dos membros em `full_data=1`).

## 6. API (rotas principais)

```
POST /auth/register (nasce pending, exceto ADMIN_RIOT_ID) · POST /auth/login · GET /me
PUT  /me/password
GET  /players · GET /roster · POST /roster (rastreado, com apelido) · DELETE /roster/... (admin)
GET  /players/{riot}/{tag}/overview|maps|agents|weapons|insights|recent|heatmap|vs-lobby|...
GET  /leaderboard · GET /matches/{id}
GET  /group/squads · POST /group/detail · GET /group/duo/... · GET /group/synergy-matrix
GET|POST /group/correlation · POST /group/pre-queue
GET  /admin/users · POST /admin/users/{id}/approve|reject|admin
GET  /status/ingest · GET /health
```
Rotas de dados exigem usuário **aprovado** (`current_user`); só `/me` aceita
pendente (`any_user`). Lista completa em [API_ROUTES.md](API_ROUTES.md).
Auth por `Authorization: Bearer <jwt>`. CORS restrito às origens do frontend.

## 7. Frontend (`frontend/src/`)

- **Vite + React 18 + react-router**. Estado de auth em contexto (`lib/auth`),
  token em localStorage, cliente HTTP fino (`lib/api`).
- **Páginas:** Login, Register, Dashboard (perfil próprio), Profile (por riot id),
  Leaderboard, Group.
- **Componentes:** `PlayerProfile` (abas), `Heatmap` (canvas + transform da
  Valorant-API), `blocks` (gating/barras/insights), `Stats`.
- **Tema "HUD tático"**: painéis chanfrados (clip-path), tipografia condensada
  (Oswald), dados em mono, paleta da UI do Valorant.

## 8. Rate limit & resiliência

- Cliente serializa chamadas com intervalo mínimo (`_MIN_INTERVAL ≈ 2,1s`),
  com lock: downloads de cadastro rodam em threads de background.
- Orçamento por execução do poller: `v4/matches` para todos; `mmr` +
  `mmr-history` só com partida nova ou a cada 6 h; deep backfill em rodízio
  até `DEEP_PER_RUN`, parando no primeiro 429.
- Toda execução vai para `ingest_runs` (chamadas, 429, erros); estado por
  jogador em `ingest_state`. O poller sai com erro sem `DATABASE_URL` ou se
  nenhum jogador pôde ser ingerido.
- Postgres atrás do pooler do Supabase: pool de conexões (`ThreadedConnectionPool`)
  e escrita em lote (`execute_values`).
- `_get` trata 429/404/erro de rede sem derrubar o processo.
- Ingestão idempotente: re-rodar o poller não duplica nem corrompe.
- Backend serve do banco → indiferente a falhas/lentidão da Henrik no runtime.

## 9. Deploy

- **Backend:** Render Web Service (oregon), root `backend`, start `uvicorn app.main:app`.
- **Frontend:** GitHub Pages via `pages.yml`, só depois do CI verde.
- **Banco:** Supabase Postgres, papel `vavapprt_app`, schema privado `vavapprt`.
- **Poller:** `poll.yml` disparado a cada 5 min pelo `pg_cron` do Supabase
  (`workflow_dispatch`), cron do GitHub como reserva, keepalive no próprio run.
- **CI:** `ci.yml` — pytest em SQLite e Postgres + build do frontend.
- Segredos via env/secrets; `.env` nunca commitado. Detalhes em
  [DEPLOY_WEB.md](DEPLOY_WEB.md).

## 10. Decisões técnicas (e porquês)

| Decisão | Porquê |
|---|---|
| Reusar núcleo Python | Cliente/processador já funcionavam; só trocamos a casca (Discord→web). |
| Ingerir v4 (não v3) | Schema mais limpo (UUIDs vs URLs), menor, com rounds+kills. |
| Chave `puuid` | Nomes mudam; puuid é estável. |
| `raw_gz` + normalizado | Recompute futuro sem re-buscar + query rápida, dentro de 500 MB. |
| `full_data` flag | Não diluir métricas profundas com backfill de resumo. |
| Cliente síncrono (`requests`) | Poller é script simples; evita complexidade de async no cron. |
| BackgroundTasks p/ add-player | Resposta rápida; download de ~30s não bloqueia o request. |

## 11. Testes & verificação

`backend/tests` (pytest, 32 testes) roda a mesma suíte em SQLite e em Postgres
(schema descartável por sessão). A HenrikDev é falsa: 4 partidas v4 reais
**anonimizadas** em `tests/fixtures`. Cobre métricas (placar oficial = derivado,
um first kill por round), aprovação e permissões, todas as rotas, ingestão
idempotente, orçamento de cota, rate limit e o religamento de quem entra no
grupo depois de já ter jogado com ele.

## 12. Trabalho futuro

- Calibrar transform do heatmap por mapa e janelas de trade/economia.
- Migrações de schema (hoje `CREATE TABLE IF NOT EXISTS`; mudanças de coluna
  exigem recriar — adotar Alembic se o schema evoluir muito).
