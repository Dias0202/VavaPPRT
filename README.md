# VavaPPRT — Plataforma de Análise de Desempenho (Valorant)

Plataforma web fechada para um grupo de amigos analisar desempenho no Valorant:
**onde cada um erra e acerta, como os grupos jogam bem, em quais mapas e quais
combinações de jogadores rendem** — atualizado automaticamente a cada partida.

Os dados vêm da [API não-oficial HenrikDev](https://docs.henrikdev.xyz/) (contorna
a inviabilidade da API oficial da Riot para projetos pessoais — ver
[docs/PESQUISA.MD](docs/PESQUISA.MD)) e os ícones/minimapas da
[Valorant-API.com](https://valorant-api.com/).

| | |
|---|---|
| **Documento de produto** | [docs/PDD.md](docs/PDD.md) |
| **Documento de design técnico** | [docs/SDD.md](docs/SDD.md) |
| **Guia de deploy (grátis)** | [docs/DEPLOY_WEB.md](docs/DEPLOY_WEB.md) |
| **Plano de análise / catálogo de métricas** | [docs/PLANO_ANALYTICS.md](docs/PLANO_ANALYTICS.md) |
| **Bot X9 do Discord (SDD + setup)** | [docs/BOT_X9.md](docs/BOT_X9.md) |
| **X9 v2: diagnóstico e plano de melhoria** | [docs/PLANO_X9_V2.md](docs/PLANO_X9_V2.md) |

---

## Arquitetura

```
   Navegador
       │
       ▼
  Frontend React (GitHub Pages) ──► Backend FastAPI (Render) ──► Postgres (Supabase)
   SPA "Telemetria"                 serve SÓ do banco                 ▲      │
                                                                      │      │ pg_cron 15 min
                                                         grava        │      ▼
                                              Poller (GitHub Actions, workflow_dispatch)
                                                                      │
                                                                      ▼
                                                       API HenrikDev (v4, 30 req/min)
```

**Bot X9 (Discord):** o poller também posta o placar de cada partida nova num
webhook do Discord; o bot X9 (roda no PC de alguém do grupo) lê o mesmo banco
pela `botapi`, zoa quem jogou mal na thread do placar, responde perguntas por
texto (`@X9`) e por voz ("X9, conta o round 13"), em pt-br e 100% free tier
(Gemini → Groq, Whisper local, edge-tts/Piper). Detalhes em
[docs/BOT_X9.md](docs/BOT_X9.md).

**Princípio central:** a API Basic da HenrikDev permite só **30 req/min**. As
páginas servem **exclusivamente do banco** — nunca chamam a Henrik no
carregamento. Quem alimenta o banco é o **poller** (cron), isolado e com rate
limit controlado.

| Camada | Tecnologia | Onde roda |
|---|---|---|
| Frontend | React + Vite (SPA) | GitHub Pages (publica com CI verde) |
| Backend | FastAPI (Python 3.12) | Render (free, oregon) |
| Banco | PostgreSQL (fallback SQLite no dev) | Supabase (free, us-west-2) |
| Ingestão | Script Python | GitHub Actions, disparado pelo `pg_cron` do Supabase |
| Testes | pytest em SQLite **e** Postgres | GitHub Actions (CI) |

## Funcionalidades

- **Grupo fechado**: cadastro novo fica pendente até o admin aprovar
  (`ADMIN_RIOT_ID` define o admin). Membros adicionam pessoas **rastreadas**
  (sem login, com apelido); só o admin remove.
- **Pré-fila**: marque quem está on → mapas para aceitar/evitar e escalação de
  agentes sem repetir agente nem função, com a base de evidência explícita
  (stack exato > duplas > individual).
- **Duplas**: juntos vs separados, **quem carrega**, **quem vinga quem** (trade
  mútuo em 4 s), forma, mapas e combos — mais a **matriz de sinergia** N×N.
- **Partida aberta**: placar dos 10, timeline de rounds com economia e quem
  abriu o round, heatmap da partida.
- **Você vs lobby / vs seu elo**: posição média no placar, % de top 1 do time,
  ACS/K/D/ADR/HS% contra o próprio time, os adversários e a média do seu tier.
- **Correlação do stack**: win rate do round quando o grupo abre vs quando
  toma a abertura, e por quantos terminam vivos.
- **Perfil com abas**: Visão · Combate · Economia · Mapas · Agentes · Armas ·
  Insights · Heatmap.
- **Combate**: K/D, ACS, ADR, HS%, **KAST**, multikills.
- **Entrada / Clutch / Trade**: first bloods/deaths, clutch 1vX reconstruído da
  timeline de kills, % de mortes vingadas vs isoladas.
- **Economia**: win rate por compra (pistol/eco/força/full) e por lado (atk/def).
- **Mapas e agentes** com classificação forte/fraco e **selo de confiança**
  (gating de amostragem — nunca vende ruído como insight).
- **Heatmap** de kills/mortes sobre o minimapa.
- **Grupos (2–5)**: detecção de quem joga junto, win rate, **lift juntos-vs-sozinho**,
  forma recente (V/D), melhores/piores mapas pra filar, composições, métricas
  profundas do grupo e **recomendação** em linguagem natural.
- **Progresso no tempo** (não só retrato): **tendência** das últimas N partidas
  vs as N anteriores, **curva de RR/elo**, **sessões** com detecção de queda de
  rendimento (tilt) e **relatório semanal** do grupo.
- **Pós-plant/retake/site**, **HS% por arma** e **uso de utilidade** por agente.
- **Metas de treino**: as lacunas do motor de insights viram número com
  acompanhamento.
- **Opt-out**: apagar conta e todos os dados pessoais associados.
- **Honestidade sobre o dado**: chip "dados há X min" (fica âmbar se o poller
  parar), barra de histórico completo, faixa "acordando o servidor".

## Estrutura do repositório

```
backend/                 FastAPI — serve só do banco
  app/
    main.py                rotas (auth, roster, perfil, grupos, leaderboard)
    db.py                  acesso a dados (Postgres + SQLite) e schema
    auth.py                senha (bcrypt) + JWT
    valorant_api.py        cliente HenrikDev v4 (síncrono, com throttle)
    metrics.py             motor de métricas (clutch/trade/KAST/lado) sobre v4
    analytics.py           agregações + grupos + gating + insights
    squad.py               stacks, correlação por round, dupla, pré-fila
    matchview.py           detalhe de partida, você vs lobby/elo
    progress.py            tendência, sessões, RR, metas, semana
    ingest.py              ingestão v4 + backfill + deep backfill em rodízio
    poll.py                entrypoint do poller
    botapi.py              tools do bot X9 (contrato para LLM) + espelho /bot/v1
    announce.py            placar das partidas novas no webhook do Discord
  tests/                   pytest (fixtures v4 anonimizadas)
  requirements.txt · requirements-dev.txt · .env.example
bot/                     bot X9 do Discord (discord.py + Gemini/Groq + Whisper/TTS)
  x9/                      llm · brain · announcer · voice · wake · stt · tts
  tests/ · requirements.txt · .env.example · run.ps1
frontend/                React SPA (tema "HUD tático")
  src/{pages,components,lib}
  package.json · .env.example · dev.ps1
.github/workflows/       ci.yml · pages.yml · poll.yml · backfill.yml · deep-backfill.yml
docs/                    PDD · SDD · DEPLOY_WEB · PLANO_ANALYTICS · PLANO_FINALIZACAO · BOT_X9
```

## Rodar localmente

**Atalho (Windows):** `.\dev.ps1` na raiz — cria a venv, instala tudo, copia os
`.env` de exemplo e sobe backend (`:8000`) e frontend (`:5173`) juntos.

Manualmente:

**Backend** (usa SQLite em `backend/data/app.db` se `DATABASE_URL` não for definida):
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env       # preencha HENRIKDEV_API_KEY
uvicorn app.main:app --reload --port 8000
```

**Frontend** (precisa de Node; sem Node no sistema, use `nodeenv` — ver dev.ps1):
```bash
cd frontend
npm install
copy .env.example .env       # VITE_API_URL=http://localhost:8000
npm run dev                  # http://localhost:5173
```

`GET /health` responde o diagnóstico da configuração — banco em uso, se a chave
da HenrikDev está presente e o tamanho do roster. `status: "degraded"` aponta o
que falta. **Sem `HENRIKDEV_API_KEY` a API recusa tudo com 401**: não dá para
cadastrar ninguém nem ingerir partidas.

**Ingestão manual:**
```bash
cd backend
python -m app.poll             # baixa partidas novas (v4) + curva de RR
python -m app.poll backfill 5  # backfill de histórico (resumo, barato)
python -m app.poll deep 50     # backfill PROFUNDO: promove resumo -> completa
python -m app.poll recompute   # recalcula métricas do raw_gz (sem tocar na API)
```

O `deep` é o que dá lastro às métricas profundas — sem ele, clutch/KAST/trade/
economia/pós-plant saem só das ~5 partidas que o `/v4/matches` devolve. Custa 1
requisição por partida (30 req/min); ao bater o rate limit ele para sozinho, é
só rodar de novo. `GET /players/.../coverage` mostra quanto já tem lastro.

## Deploy

Passo a passo (Supabase + Render + GitHub Pages + GitHub Actions, 100% grátis)
e runbook de "o site não carrega" em **[docs/DEPLOY_WEB.md](docs/DEPLOY_WEB.md)**.

## Privacidade

Análise exibida só para membros do grupo (consentidos). Dados de adversários
entram apenas de forma agregada (contexto de rank), nunca como perfil navegável —
em linha com a política da Riot contra *scouting* não consentido.
