# Deploy da Plataforma Web (100% grátis)

Arquitetura:

```
   Navegador
       │
       ▼
  Frontend React  ──►  Backend FastAPI  ──►  Supabase (Postgres)
   (Vercel)            (Render free)              ▲
                                                  │ grava partidas
                                       Poller (GitHub Actions cron, ~10min)
                                                  │
                                                  ▼
                                          API HenrikDev (30 req/min)
```

Regra de ouro: **o frontend e o backend só leem do banco.** Quem fala com a
HenrikDev é o poller, isolado e com rate limit controlado. Assim ninguém leva
429 ao abrir uma página.

Você vai precisar de contas grátis em: **Supabase**, **Render**, **Vercel**,
**GitHub** e uma **Basic Key** da HenrikDev.

---

## 1. Banco de dados — Supabase

1. Crie um projeto em [supabase.com](https://supabase.com/). Guarde a senha do banco.
2. **Project Settings → Database → Connection string → URI**. Copie a URL
   (`postgresql://postgres.[ID]:[SENHA]@...pooler.supabase.com:6543/postgres`).
3. Se a senha tiver caracteres especiais (`@`, `#`...), troque por uma simples
   ou faça URL-encode. Guarde essa URL: é o `DATABASE_URL`.

O schema é criado sozinho na primeira execução (backend ou poller). Nada a rodar
à mão.

## 2. Chave da API — HenrikDev

1. Entre no Discord da HenrikDev e gere uma **Basic Key** (instruções em
   [docs.henrikdev.xyz](https://docs.henrikdev.xyz/)). Limite: 30 req/min.
2. Guarde: é o `HENRIKDEV_API_KEY`.

## 3. Backend — Render

1. Suba o repositório no GitHub (o `.gitignore` já protege segredos).
2. Em [render.com](https://render.com/): **New + → Web Service** → conecte o repo.
3. Configurações:
   - **Root Directory:** `backend`  ← importante, não deixe na raiz
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
4. **Environment** → adicione:
   - `DATABASE_URL` = URL do Supabase
   - `HENRIKDEV_API_KEY` = sua Basic Key
   - `JWT_SECRET` = um valor aleatório longo (ex.: saída de um gerador de senha)
   - `CORS_ORIGINS` = a URL do Vercel (ex.: `https://vavapprt.vercel.app`) —
     pode deixar `http://localhost:5173` no começo e atualizar depois
   - `DEFAULT_REGION` = `latam` (ou `br`, `na`, `eu`, `ap`, `kr`)
5. Crie o serviço. A URL pública (ex.: `https://vavapprt-api.onrender.com`) é o
   seu `VITE_API_URL`.

> O plano free dorme após 15 min ocioso e acorda no 1º acesso (alguns segundos).
> Como o backend só lê do banco, isso é tranquilo.

## 4. Frontend — Vercel

1. Em [vercel.com](https://vercel.com/): **Add New → Project** → importe o repo.
2. Configurações:
   - **Root Directory:** `frontend`
   - **Framework Preset:** Vite (detecta sozinho)
   - **Build Command:** `npm run build` · **Output:** `dist`
3. **Environment Variables:**
   - `VITE_API_URL` = URL do backend no Render
4. Deploy. Pegue a URL final e **volte ao Render** para colocá-la em
   `CORS_ORIGINS` (separe múltiplas por vírgula).

## 5. Poller — GitHub Actions

O workflow já está em `.github/workflows/poll.yml` (roda a cada 10 min).

1. No GitHub: **Settings → Secrets and variables → Actions → New repository secret**:
   - `DATABASE_URL` = mesma URL do Supabase
   - `HENRIKDEV_API_KEY` = sua Basic Key
2. (Opcional) Em **Variables**, crie `DEFAULT_REGION`.
3. Teste agora: aba **Actions → Poller de partidas → Run workflow**. Veja o log:
   deve dizer `OK: N usuários, M partidas novas.`

> O cron do GitHub só dispara em repositórios com atividade e pode atrasar
> alguns minutos sob carga — normal. Para um grupo de ≤10, 10 min sobra.

---

## Backfill inicial (histórico)

Logo após cada amigo se cadastrar, rode **uma vez** o backfill de resumo para
popular K/D, vitória, mapas e agentes de partidas antigas (o cron só pega as
novas daí pra frente):

```bash
cd backend
python -m app.poll backfill 5     # 5 páginas ~= 100 partidas por jogador
```

Métricas profundas (clutch, KAST, entrada, economia, heatmap) só existem para
partidas **completas** capturadas pela ingestão v4 (recentes + futuras) — o
backfill é resumo. A plataforma separa as duas coisas e mostra um selo quando
faltam partidas completas.

## ⚠️ Primeira execução no Postgres

Todo o desenvolvimento rodou em SQLite. Na **primeira vez no Supabase**, confira:
1. `init_db` cria o schema sozinho no startup do backend — veja nos logs do
   Render "Banco inicializado." sem erro.
2. O `raw_gz` usa `BYTEA`; se o primeiro poller gravar e o painel ler sem erro,
   o caminho `bytea` está ok.
3. Rode o backfill acima apontando para a `DATABASE_URL` do Supabase.

## Pôr pra rodar

1. Abra a URL do Vercel → **Criar conta** (usuário, senha, Riot ID#Tag).
2. Rode o backfill (acima) para já ter histórico.
3. Jogue uma competitiva. No próximo ciclo do poller (≤10 min), a partida
   completa aparece no painel com as métricas profundas.
4. Cada amigo cria a própria conta. Quem estiver na mesma partida é gravado
   junto (uma chamada cobre o squad), alimentando a aba **Grupo**.

## Rodar local (dev)

**Backend:**
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env        # preencha HENRIKDEV_API_KEY (DATABASE_URL é opcional: sem ela usa SQLite)
uvicorn app.main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
copy .env.example .env        # VITE_API_URL=http://localhost:8000
npm run dev                   # abre em http://localhost:5173
```

> Sem Node instalado no sistema? Dá pra ter Node dentro de um venv:
> `pip install nodeenv && nodeenv .nodeenv` e use `.nodeenv\Scripts\npm`.

**Poller manual:**
```bash
cd backend
python -m app.poll
```
