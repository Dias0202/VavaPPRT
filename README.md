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

---

## Arquitetura

```
   Navegador
       │
       ▼
  Frontend React (Vercel)  ──►  Backend FastAPI (Render)  ──►  Postgres (Supabase)
   SPA, tema HUD tático        serve SÓ do banco                     ▲
                                                                     │ grava
                                              Poller (GitHub Actions cron ~10min)
                                                                     │
                                                                     ▼
                                                       API HenrikDev (v4, 30 req/min)
```

**Princípio central:** a API Basic da HenrikDev permite só **30 req/min**. As
páginas servem **exclusivamente do banco** — nunca chamam a Henrik no
carregamento. Quem alimenta o banco é o **poller** (cron), isolado e com rate
limit controlado.

| Camada | Tecnologia | Onde roda |
|---|---|---|
| Frontend | React + Vite (SPA) | Vercel |
| Backend | FastAPI (Python 3.12) | Render (free) |
| Banco | PostgreSQL (fallback SQLite no dev) | Supabase (free) |
| Ingestão | Script Python via cron | GitHub Actions |

## Funcionalidades

- **Login por usuário** + **adicionar pessoas ao grupo** (rastreadas, sem login):
  ao adicionar, os dados são baixados e os grupos se formam automaticamente.
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
    ingest.py              ingestão v4 + backfill (lifetime)
    poll.py                entrypoint do cron
  requirements.txt · Procfile · .env.example
frontend/                React SPA (tema "HUD tático")
  src/{pages,components,lib}
  package.json · .env.example · dev.ps1
.github/workflows/       poll.yml (cron 10min) · backfill.yml (manual)
docs/                    PDD · SDD · DEPLOY_WEB · PLANO_ANALYTICS · PESQUISA
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
python -m app.poll            # baixa partidas novas (v4)
python -m app.poll backfill 5 # backfill de histórico (resumo)
```

## Deploy

Passo a passo (Supabase + Render + Vercel + GitHub Actions, 100% grátis) em
**[docs/DEPLOY_WEB.md](docs/DEPLOY_WEB.md)**.

## Privacidade

Análise exibida só para membros do grupo (consentidos). Dados de adversários
entram apenas de forma agregada (contexto de rank), nunca como perfil navegável —
em linha com a política da Riot contra *scouting* não consentido.
