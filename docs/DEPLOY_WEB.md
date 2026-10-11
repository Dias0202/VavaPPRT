# Deploy da Plataforma Web (100% grátis)

```
   Navegador
       │
       ▼
  Frontend React ──► Backend FastAPI ──► Supabase Postgres (schema `vavapprt`)
  (GitHub Pages)     (Render free)              ▲          │
                                                │ grava    │ pg_cron a cada 15 min
                                                │          ▼
                              Poller (GitHub Actions) ◄── workflow_dispatch
                                                │
                                                ▼
                                    API HenrikDev (30 req/min)
```

Regra de ouro: **o frontend e o backend só leem do banco.** Quem fala com a
HenrikDev é o poller, isolado e com orçamento de chamadas. Ninguém leva 429 ao
abrir uma página.

| Peça | Onde | Por quê |
|---|---|---|
| Banco | Supabase free | Não expira (o Postgres free do Render expira em 30 dias — foi o que derrubou a produção em set/2026) |
| API | Render free (`oregon`) | Mesma região do Supabase (`us-west-2`): ~1 ms por query |
| Site | GitHub Pages | Sem conta extra; publica só com CI verde |
| Poller | GitHub Actions | Disparado pelo `pg_cron` do Supabase a cada 15 min (o cron do GitHub atrasa horas em repo free) |

---

## 1. Banco — Supabase

1. Crie (ou restaure) o projeto. Região sugerida: `us-west-2`.
2. Crie um papel só da aplicação e um schema privado — fora do `public`, que é
   exposto pela API REST do Supabase:
   ```sql
   create role vavapprt_app login password '<senha-forte>' connection limit 20;
   grant vavapprt_app to postgres;
   create schema vavapprt authorization vavapprt_app;
   alter role vavapprt_app set search_path = vavapprt;
   revoke all on schema vavapprt from public;
   ```
3. `DATABASE_URL` = URI do **session pooler** (IPv4 — Render e GitHub Actions
   não falam IPv6), com o usuário no formato `papel.<ref-do-projeto>`:
   ```
   postgresql://vavapprt_app.<ref>:<senha>@aws-1-us-west-2.pooler.supabase.com:5432/postgres?sslmode=require
   ```
   > O pooler guarda credencial em cache por alguns segundos: depois de trocar a
   > senha, a nova pode demorar ~10 s para valer.

O schema (tabelas, colunas novas) é criado sozinho no startup da API e em toda
execução do poller. Nada a rodar à mão.

## 2. Chave da API — HenrikDev

Gere uma **Basic Key** no Discord da HenrikDev ([docs](https://docs.henrikdev.xyz/)).
Limite: 30 req/min — dá para ~10 jogadores com o orçamento do poller.

## 3. Backend — Render

O `render.yaml` na raiz é um Blueprint: **New + → Blueprint** → conecte o repo.
Ele pede os segredos:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | URI do session pooler (passo 1) |
| `HENRIKDEV_API_KEY` | Basic Key |
| `ADMIN_RIOT_ID` | `Nome#TAG` de quem administra (ex.: `YFV#4258`) |
| `JWT_SECRET` | gerado pelo Render — a API **não sobe** com o valor de exemplo |
| `CORS_ORIGINS` | `https://<usuario>.github.io` |

`GET /health` diz se está tudo ligado (`database_ok`, `henrikdev_key`,
`admin_configured`) e quando o poller rodou por último (`last_ingest`).

> **Não** mantenha a API acordada com ping: as horas grátis do Render (750/mês)
> são por conta, e um serviço ligado 24/7 consome quase tudo — os outros
> serviços da conta seriam suspensos. O site mostra "acordando o servidor"
> enquanto o Render sobe (até ~1 min).

## 4. Frontend — GitHub Pages

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → Variables**: `VITE_API_URL` =
   URL do Render.
3. O workflow `pages.yml` publica depois que o **CI** passa na `main`.

## 5. Poller — GitHub Actions + pg_cron

1. **Secrets** do repositório: `DATABASE_URL` e `HENRIKDEV_API_KEY` (os mesmos
   da API). Sem `DATABASE_URL` o poller **falha** de propósito — antes ele
   gravava num SQLite descartável e ficava verde.
2. **Variables** (opcionais): `DEFAULT_REGION=na`, `DEEP_PER_RUN=10`.
3. Gatilho de 15 em 15 min pelo Supabase (o cron do GitHub é só reserva):
   - crie um **PAT fine-grained** só deste repositório, permissão
     **Actions: Read and write**;
   - no Supabase, extensões `pg_cron` e `pg_net`, e as funções
     `vavapprt.set_dispatch_token(text)` / `vavapprt.dispatch_poller()`
     (guardam o PAT no Vault e chamam `workflow_dispatch`);
   - grave o token sem colá-lo em SQL de migração:
     `select vavapprt.set_dispatch_token('<PAT>')` numa conexão como `vavapprt_app`;
   - agende: `select cron.schedule('vavapprt-poller', '*/15 * * * *', $$select vavapprt.dispatch_poller()$$);`
4. **Keepalive**: todo run do poller reativa o próprio workflow
   (`gh workflow enable`), zerando o contador de 60 dias sem commit que o
   GitHub usa para desligar crons.

Cada execução fica registrada (`ingest_runs`) e aparece em **Admin → Execuções
do poller** e no chip "dados há X min" do topo do site.

### Orçamento por execução (Basic Key)

| Chamada | Quando | Custo |
|---|---|---|
| `/v4/matches` | todo jogador, toda execução | 1 por jogador |
| `/v2/mmr` + `/v1/mmr-history` | só se teve partida nova ou passou 6 h | 2 por jogador |
| `/v4/match/{id}` (deep backfill) | rodízio entre jogadores | até `DEEP_PER_RUN` |

Com 10 jogadores: ~10–40 chamadas, ~1–2 min por execução.

---

## Pôr pra rodar

1. Quem está em `ADMIN_RIOT_ID` se cadastra primeiro — nasce admin e aprovado.
2. Os amigos se cadastram e ficam **pendentes**; o admin aprova em **Admin**.
   Aprovar dispara o download do histórico (≈100 partidas-resumo + recentes).
3. Quem não quer login entra como **rastreado** (Grupo → Adicionar pessoa, com
   apelido). Só o admin remove rastreados.
4. O poller promove o histórico de resumo para partidas completas aos poucos
   (`DEEP_PER_RUN` por execução). O perfil mostra a barra "Histórico completo".

## Rodar local (dev)

`.\dev.ps1` na raiz sobe tudo. Manualmente:

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env          # sem DATABASE_URL usa SQLite local
uvicorn app.main:app --reload
python -m pytest -q tests       # SQLite; com TEST_DATABASE_URL=... roda no Postgres
python -m app.poll --allow-sqlite
```

```bash
cd frontend
npm install
copy .env.example .env          # VITE_API_URL=http://localhost:8000
npm run dev
```

## Runbook — "o site não carrega"

1. `GET https://<api>/health`:
   - não responde em ~1 min → Render fora (dashboard → Logs);
   - `database_ok: false` → Supabase pausado/fora (dashboard → Restore) ou senha trocada;
   - `henrikdev_key: false` → falta a env no Render.
2. Dados velhos ("dados há X h" em âmbar) → aba **Actions → Poller de partidas**:
   - run vermelho → o log diz o motivo (chave, banco, rate limit);
   - nenhum run recente → `select * from cron.job_run_details order by start_time desc limit 5;`
     e `select * from net._http_response order by id desc limit 5;` no Supabase
     (401 = PAT expirou: gere outro e rode `set_dispatch_token`).
3. Perfil sem métricas profundas → **Admin → Roster**: veja "Completas"; o
   deep backfill sobe esse número a cada execução.
