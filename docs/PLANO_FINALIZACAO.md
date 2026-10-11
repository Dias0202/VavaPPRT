# VavaPPRT — Diagnóstico e Plano de Finalização

> Gerado em 2026-10-11 a partir da leitura do repositório, execução local de
> todos os endpoints, build do frontend e inspeção do deploy (Render, GitHub
> Actions, GitHub Pages). Decisões de produto tomadas em entrevista com o dono
> do projeto (seção 3).

**Objetivo declarado:** uma plataforma perfeita para analisar jogatinas solo e,
principalmente, **derivações em grupo** — ser melhor que o tracker.gg para um
grupo fechado de 6–10 amigos.

---

## 1. Resumo executivo

O **código está saudável** e o motor de análise funciona. O **ambiente de
produção está quebrado**, e de um jeito silencioso: tudo parece "verde", mas
nenhum dado chega ao usuário.

| Camada | Estado | Evidência |
|---|---|---|
| Backend (código) | ✅ Funciona | 28/28 endpoints `200` contra SQLite local (96 partidas completas) |
| Métricas | ✅ Consistentes | `/validate/metrics?limit=20` → 0 divergências |
| Frontend (código) | ✅ Builda | `vite build` ok, 361 KB JS (114 KB gzip) |
| Frontend (deploy) | ✅ No ar | `dias0202.github.io/VavaPPRT` → HTTP 200 |
| API em produção | ❌ Degradada | `/health` → `database_ok:false`, `henrikdev_key:false`, roster 0 |
| Banco em produção | ❌ Perdido | Postgres free do Render criado ~09/08 — plano free expira em 30 dias |
| Poller | ❌ Inútil e desativado | Sem secret `DATABASE_URL` → gravava em SQLite descartável ("Roster vazio"); workflow agora `disabled_inactivity` |
| Testes | ❌ Inexistentes | Nenhum arquivo de teste no repo; Postgres nunca exercitado fora de produção |

---

## 2. Diagnóstico detalhado

### 2.1 O que está funcionando

- **Ingestão v4** (`ingest.py`): partidas completas, resumo lifetime, deep
  backfill por match-id, curva de RR, pico de rank por ato, `recompute` offline
  a partir do `raw_gz`. Idempotente por `match_id`.
- **Motor de métricas** (`metrics.py`): K/D, ACS, ADR, HS%, KAST, first
  blood/death, clutch 1vX, trade, economia por tipo de compra, lado atk/def,
  pós-plant/retake, utilidade.
- **Analytics** (`analytics.py`, `progress.py`): mapas/agentes/armas com gating
  de amostra, heatmap, insights em linguagem natural, tendência, sessões/tilt,
  metas, relatório semanal, plano de fila por rotação de mapas.
- **Frontend**: login/registro, dashboard, perfil com abas, grupo, ranking,
  semana, metas, conta (opt-out), rotação de mapas (admin), timeout de
  requisição com mensagem clara.

### 2.2 O que está quebrado

| # | Problema | Impacto | Causa |
|---|---|---|---|
| P1 | Banco de produção inexistente | Site não funciona para ninguém | Render free Postgres expira em 30 dias |
| P2 | `HENRIKDEV_API_KEY` ausente no Render | Cadastro devolve 503 | `sync: false` no blueprint nunca foi preenchido |
| P3 | Secret `DATABASE_URL` ausente no GitHub | Poller grava num SQLite efêmero e "passa" | Secret nunca criado; não há checagem que falhe |
| P4 | Poller desativado | Nenhuma partida nova entra | GitHub desliga cron após 60 dias sem commit (último: 09/08) |
| P5 | Cron "a cada 10 min" roda a cada ~5h | Dados atrasados | Throttling de schedule do GitHub em repo free |
| P6 | Postgres nunca testado | Risco de erro de SQL na 1ª ida a produção | Todo dev rodou em SQLite |

### 2.3 Bugs e dívidas de código

| # | Item | Onde | Severidade |
|---|---|---|---|
| B1 | `group_correlation` exige o **roster inteiro** no mesmo time — com 6–10 pessoas (máx. 5 por time) **nunca** retorna dados; com 1 pessoa conta partidas solo como "juntos" | `analytics.py:722` | Alta |
| B2 | `round_correlation` mistura partidas solo e mede vitória **da partida**, não do round | `analytics.py:780` | Média |
| B3 | Cadastro aberto: qualquer Riot ID válido vira membro e vê tudo | `main.py:/auth/register` | Alta |
| B4 | Qualquer membro remove rastreados (apaga dados) | `main.py:DELETE /roster` | Alta |
| B5 | Admin = "primeiro a se cadastrar" (corrida após deploy limpo) | `main.py:register` | Alta |
| B6 | `JWT_SECRET` tem default inseguro se a env faltar | `config.py` | Média |
| B7 | `DEFAULT_REGION` inconsistente: `na` (var GitHub, render.yaml), `latam` (workflows/doc), `br` (.env local) | vários | Baixa |
| B8 | `@app.on_event` e `datetime.utcnow()` depreciados | `main.py`, `auth.py`, `db.py` | Baixa |
| B9 | Poller chama `mmr` + `mmr-history` para todo jogador a cada execução (desperdício de cota) | `ingest.py:ingest_all` | Média (com 10 jogadores) |
| B10 | `TestClient` exige `httpx2`, que não está em requirements (dev) | `requirements.txt` | Baixa |
| B11 | Docs descrevem Supabase + Vercel; deploy real é Render DB + Pages | `README`, `DEPLOY_WEB`, `SDD` | Baixa |

### 2.4 Backend pronto sem tela

Endpoints que existem e funcionam, mas que o frontend nunca chama:
`/group/synergy`, `/group/duo/...`, `/group/head-to-head/...`,
`/group/round-analysis`, `/players/.../spatial`, `/players/.../coverage`,
`DELETE /roster/...`, `/validate/metrics`. É profundidade de grupo que já foi
paga e não chega ao usuário.

---

## 3. Decisões tomadas

| Tema | Decisão |
|---|---|
| Banco | **Supabase free** (Postgres, 500 MB, não expira) |
| Dados existentes | **Começar do zero** em produção (SQLite local fica só para dev) |
| Poller | **GitHub Actions + keepalive** contra a desativação de 60 dias + **gatilho externo** (cron-job.org → `workflow_dispatch`) a cada ~15 min |
| Hospedagem | **Render free** (API) + **GitHub Pages** (SPA), com estado "servidor acordando" na UI |
| Acesso | **Admin aprova cadastros** (conta nasce `pending`) |
| Rastreados | **Membro adiciona, só admin remove** |
| Admin inicial | Variável **`ADMIN_RIOT_ID`** (ex.: `YFV#4258`) — nasce admin e aprovado |
| Tamanho do grupo | **6 a 10** jogadores → Basic Key (30 req/min) com orçamento por execução |
| Região | Sempre a região que a conta devolve; fallback único `na` em todos os lugares |
| Escopo extra | Testes + CI · status da ingestão na UI · deep backfill automático · docs atualizadas |
| Diferenciais v1 | Duplas (sinergia + H2H) · detalhe de partida do grupo · pré-fila "quem joga hoje" · comparação com o lobby/rank |

---

## 4. Plano de execução

Ordem pensada para ter **produção funcionando primeiro** (fase 0–1), depois
**confiança** (fase 2), depois **diferenciais** (fase 3–4). Cada fase termina
com algo verificável.

### Fase 0 — Produção de pé (bloqueante)

1. **Supabase**: criar projeto; pegar a URI do *pooler* (porta 6543, modo
   session/transaction) e URL-encodar a senha.
2. **Render**:
   - remover o bloco `databases:` do `render.yaml`; `DATABASE_URL` vira `sync: false`;
   - preencher `DATABASE_URL`, `HENRIKDEV_API_KEY`, `ADMIN_RIOT_ID`;
   - unificar `DEFAULT_REGION=na`.
3. **GitHub**: criar secret `DATABASE_URL`; reativar o workflow do poller.
4. **Poller falha alto**: `poll.py` aborta com exit 1 se `DATABASE_URL` estiver
   vazia fora do modo dev (flag `--allow-sqlite`). Nunca mais "verde" falso.
5. **Smoke test de Postgres**: subir a API local apontando para o Supabase e
   rodar o script de smoke dos 28 endpoints antes do primeiro cadastro real.

✅ **Pronto quando:** `/health` em produção → `status: ok`, `database: postgres`,
`henrikdev_key: true`.

### Fase 1 — Acesso e roster (segurança)

1. Coluna `users.status` (`pending` | `approved`) + `ADMIN_RIOT_ID` decide o admin.
   - `current_user` bloqueia `pending` (403 com mensagem "aguardando aprovação");
   - login de `pending` devolve token limitado só para `/me`.
2. Rotas admin: `GET /admin/pending`, `POST /admin/users/{id}/approve|reject`.
   Ingestão do novo jogador só dispara **após aprovação** (economiza cota).
3. `DELETE /roster/...` exige admin; `POST /roster` continua para qualquer membro.
4. Tela "Admin" no frontend: fila de aprovação + roster com botão remover.
5. `JWT_SECRET` obrigatório em produção (falha no startup se estiver no default).

✅ **Pronto quando:** um cadastro novo não vê nada até o admin aprovar; membro
comum não consegue remover rastreados (teste automatizado cobre).

### Fase 2 — Ingestão confiável + testes + CI

1. **Poller em produção**:
   - job `keepalive` (commit vazio mensal ou action de keepalive) contra a desativação;
   - cron-job.org chamando `workflow_dispatch` a cada 15 min (token fine-grained
     só com `actions:write`) e pingando `/health` (reduz hibernação do Render).
2. **Orçamento de cota** (10 jogadores, 30 req/min, execução ≤ 5 min):
   - `v4/matches` para todos (~10 chamadas);
   - `mmr` + `mmr-history` só se o jogador teve partida nova ou passou 6h (B9);
   - **deep backfill automático**: até ~10 partidas-resumo por execução, em
     rodízio entre jogadores; para no primeiro 429.
3. **Tabela `ingest_runs`** (início, fim, partidas novas, erros, 429s) e
   `players.last_ingested_at` → expostos em `GET /status/ingest`.
4. **Testes (pytest)**:
   - fixtures com 2–3 partidas v4 reais (raw_gz anonimizado) em `backend/tests/fixtures`;
   - unidade: `metrics.py` (clutch, trade, KAST, lado) com valores conferidos à mão;
   - API: todos os endpoints via `TestClient` (status + shape), auth/aprovação/admin;
   - mesmo suite rodando em **SQLite e Postgres** (service container no CI).
5. **CI** (`.github/workflows/ci.yml`): pytest (matriz sqlite/postgres) +
   `npm ci && npm run build` em todo push/PR. Pages só publica se CI passar.
6. Dívidas B6, B8, B10 corrigidas aqui; `requirements-dev.txt` com pytest/httpx.

✅ **Pronto quando:** CI verde nas duas bases; partida jogada aparece no site em
≤ 20 min; `/status/ingest` mostra a última execução.

### Fase 3 — UX de dados honesta

1. **Status da ingestão na UI**: "atualizado há X min", jogador "baixando…",
   barra de cobertura do deep backfill (`/coverage`), aviso se o poller falhou
   na última execução.
2. **Servidor acordando**: em vez de erro após timeout, tela "acordando o
   servidor (até ~50s)" com retry automático.
3. Corrigir **B1/B2**: correlação de grupo passa a receber o **subconjunto
   escolhido** (2–5 membros) e a medir round-win quando o dado for de round.

✅ **Pronto quando:** nenhuma tela mostra vazio sem explicar o porquê.

### Fase 4 — Diferenciais vs tracker.gg (foco em grupo)

| Feature | Backend | Frontend | Notas |
|---|---|---|---|
| **Duplas: sinergia + head-to-head** | Pronto (`/group/duo`, `/group/head-to-head`, `/group/synergy`) | Nova página `/dupla/:a/:b` + matriz de sinergia N×N na página do grupo | WR juntos vs separados, quem carrega, trades mútuos, mapas/agentes da dupla, com selo de confiança |
| **Detalhe de partida do grupo** | Novo `GET /matches/{id}` (lê `raw_gz` + tabelas) | Página `/partida/:id`: placar 10 jogadores, timeline de rounds (econ, plant, quem morreu primeiro), heatmap da partida, destaque do grupo | Links a partir de "recentes" e do relatório semanal. Adversários só como linha do placar (sem perfil navegável — política da Riot) |
| **Pré-fila "quem joga hoje"** | Estender `queue_plan` para receber os membros selecionados | Seletor de presentes → mapas a aceitar/evitar, agente/função sugerida por pessoa, sem conflito de agente | Usa o histórico dessa combinação exata + fallback por duplas |
| **Comparação com o lobby/rank** | Novo `GET /players/.../vs-lobby`: médias do mesmo tier calculadas a partir dos 10 jogadores de cada partida já gravada | Nova aba no perfil: ACS/KAST/ADR/HS% vs média do seu elo e vs lobby da partida | Gating: só mostra tier com amostra mínima |

Ordem sugerida: Duplas → Detalhe de partida → Pré-fila → vs Lobby (do mais
pronto para o mais novo).

✅ **Pronto quando:** cada feature tem teste de API + aparece no CI + foi
validada com dados reais do grupo.

### Fase 5 — Documentação

- `README` e `DEPLOY_WEB.md`: Supabase + Render + Pages + cron-job.org, com o passo
  a passo real, incluindo `ADMIN_RIOT_ID` e a aprovação.
- `SDD.md`: aprovação, `ingest_runs`, orçamento de cota, novas rotas.
- `API_ROUTES.md`: rotas novas (admin, status, matches, vs-lobby).
- Runbook curto: "o site não carrega" → checar `/health`, `/status/ingest`, Actions.

---

## 5. Riscos

| Risco | Mitigação |
|---|---|
| Supabase pausa após 7 dias sem atividade | O poller grava a cada 15 min, o que mantém o projeto ativo |
| HenrikDev (API não-oficial) muda schema ou cai | `raw_gz` + `recompute` permitem corrigir sem refazer chamadas; testes com fixtures pegam regressões |
| 10 jogadores esgotando 30 req/min | Orçamento por execução (Fase 2) + rodízio; Advanced Key (90 req/min) como plano B |
| cron-job.org fora do ar | O cron do GitHub continua como fallback (mais lento) |
| 500 MB do Supabase | ~13 KB/partida de raw → dezenas de milhares de partidas; monitorar no `/health` |

---

## 6. Checklist de "100% funcional"

- [x] `/health` em produção `ok` com Postgres + chave
- [x] Cadastro → aprovação do admin → perfil populado em < 2 min
- [x] Partida nova entra sozinha (pg_cron → poller a cada 15 min)
- [x] Deep backfill progride sozinho (`DEEP_PER_RUN` por execução)
- [x] CI verde (SQLite + Postgres + build); Pages só publica com CI verde
- [x] Status de ingestão/cobertura visível (chip, Admin, barra no perfil)
- [x] Duplas, detalhe de partida, pré-fila e vs-lobby no ar
- [x] Docs batem com a arquitetura real

---

## 7. Execução (2026-10-11)

Implementado e em produção no mesmo dia, commits `9890996`…`HEAD`.

**Infra**
- Supabase `vavapprt-db` restaurado; papel `vavapprt_app` + schema privado
  `vavapprt` (fora do `public` exposto pela API REST). Advisors de segurança: 0.
- Render: env vars novas (`DATABASE_URL`, `HENRIKDEV_API_KEY`, `ADMIN_RIOT_ID`);
  Postgres do Render removido do blueprint.
- GitHub: secrets `DATABASE_URL`/`HENRIKDEV_API_KEY`, poller reativado,
  `ci.yml` novo, `pages.yml` atrás do CI.
- `pg_cron` (`vavapprt-poller`, `*/15`) + `pg_net` disparando o workflow; PAT no Vault.

**Bugs achados no caminho (todos com teste)**
- Pool: conexão do psycopg2 não aceita atributo → pool esgotava (pego pela suíte em Postgres).
- Quem entra no grupo depois ficava sem as partidas já gravadas com o grupo.
- Backfill de resumo pulava partida compartilhada para o 2º amigo (stack sumia).
- Throttle da HenrikDev sem lock: cadastros em paralelo estouravam 30 req/min.
- Backfills (resumo e profundo) desistiam no 1º 429 sem avisar.
- Heatmap: minimapa vazava e o canvas ficava escondido (perfil e partida).
- `head_to_head` contava partidas em times opostos como "juntos".

**Grupo inicial**: gabriel (YFV#4258, admin) com login; Luan, Gustavo, Daniel,
Mateus, Lucas e Joao como rastreados com apelido.

**Pendências conhecidas (não bloqueiam)**
- O PAT do pg_cron expira na data definida na criação — renovar com
  `select vavapprt.set_dispatch_token('<novo>')` (runbook em DEPLOY_WEB).
- Supabase free pausa após 7 dias sem atividade; o poller a cada 15 min
  deve mantê-lo ativo — conferir na primeira semana.
- Senha temporária do admin está no `.env` da raiz (fora do git): trocar em Conta → Senha.
