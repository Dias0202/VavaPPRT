# VavaPPRT — Plano da Plataforma de Análise de Desempenho

> Documento de planejamento. Baseado em **dissecação de dados reais** da API
> HenrikDev (conta YFV#4258, NA), não em suposição. Dumps crus em `_api_dump/`
> (gitignored). Objetivo: sair de um placar simples para uma plataforma que mostra
> **onde o jogador erra, onde acerta, como o grupo joga bem, em quais mapas e
> quais combinações de amigos rendem**.

---

## 1. Visão

Três perguntas que a plataforma precisa responder com dados:

1. **Indivíduo** — onde cada jogador ganha e perde rounds? (entrada, clutch,
   trade, economia, mira, posicionamento, lado, mapa, agente)
2. **Grupo** — quais duplas/trios/5-stacks vencem mais, e *por quê* (o que
   caracteriza as sessões boas vs ruins)?
3. **Contexto** — quais mapas e composições o grupo domina ou sofre?

Princípio que atravessa tudo: **é um grupo de amigos, não um dataset profissional.**
Toda afirmação ("seu melhor mapa", "essa dupla rende") passa por um **piso de
amostragem** e ganha um selo de confiança. Nunca vendemos ruído como insight
(ver §6).

---

## 2. Análise exploratória da API (dicionário de dados)

O que cada endpoint entrega de fato (HenrikDev v3/v4 + auxiliares):

### 2.1 `/v1/account/{name}/{tag}`
`puuid` · `region` · `account_level` · `name`/`tag` · `card` (ids+urls) ·
`last_update_raw`. → Identidade estável e card visual.

### 2.2 `/v2/mmr/{region}/{name}/{tag}`
- `current_data`: `currenttier`, `currenttierpatched`, `ranking_in_tier` (RR),
  `mmr_change_to_last_game`, `elo`, `games_needed_for_rating`.
- `highest_rank`: `tier`, `patched_tier`, `season`.
- `by_season`: **por ato** (`e10a3`...): `wins`, `number_of_games`,
  `final_rank`, `act_rank_wins[]`. → Trajetória de rank ao longo de anos.

### 2.3 `/v1/mmr-history/{region}/{name}/{tag}`
Lista por partida: `match_id`, `map`, `currenttier`, `ranking_in_tier`,
`mmr_change_to_last_game`, `elo`, `date_raw`. → **RR ganho/perdido por jogo**
(curva de elo no tempo).

### 2.4 `/v4/matches/{region}/{platform}/{name}/{tag}` — **fonte canônica**
Schema mais limpo que o v3 (UUIDs no lugar de 7 URLs de asset por jogador).
Top-level: `metadata`, `players[10]`, `teams[2]`, `rounds[]`, `kills[]`,
`observers`, `coaches`.

- **metadata**: `match_id`, `map{id,name}`, `game_length_in_ms`, `started_at`,
  `is_completed`, `queue{id,name,mode_type}`, `season{id,short}`, `platform`,
  `region`, `cluster`, `party_rr_penaltys[]`.
- **players[]** (todos os 10): `puuid`, `name`, `tag`, `team_id`,
  **`party_id`** (quem entrou junto na fila), `agent{id,name}`,
  `stats{score,kills,deaths,assists,headshots,bodyshots,legshots,
  damage{dealt,received}}`, `ability_casts{grenade,ability1,ability2,ultimate}`,
  `tier{id,name}` (rank na hora), `account_level`, `behavior{afk_rounds,
  friendly_fire,rounds_in_spawn}`, `economy{spent,loadout_value}`.
- **teams[]**: `team_id`, `won`, `rounds{won,lost}`.
- **rounds[]** (cada round): `id`, `result` (Elimination/Defuse/Detonate/
  Surrendered…), `ceremony`, `winning_team`, `plant{round_time_in_ms,site(A/B/C),
  location{x,y},player,player_locations[]}`, `defuse{…}`,
  **`stats[]`** (por jogador no round): `player`, `ability_casts`,
  `damage_events[]{player,bodyshots,headshots,legshots,damage}`,
  `stats{score,kills,headshots,bodyshots,legshots}`,
  **`economy{loadout_value,remaining,weapon{id,name},armor}`**,
  `was_afk`, `received_penalty`, `stayed_in_spawn`.
- **kills[]** (126 numa partida típica): `kill_time_in_round`,
  `kill_time_in_match`, `round`, `killer{puuid,team}`, `victim{puuid,team}`,
  `victim_death_location{x,y}`, `weapon{name}`, `secondary_fire_mode`,
  **`player_locations_on_kill[]`** (posição x/y + ângulo de todos os vivos),
  `assistants[]`.

> O v3 tem a mesma informação com nomes de campo diferentes (`damage_made`,
> `character`, `stats.kills`...). Vamos ingerir do **v4** e manter um adaptador
> caso algum dado só exista no v3.

### 2.5 `/v1/lifetime/matches/{region}/{name}/{tag}?size=N&page=P`
Resumo paginado por partida: `meta{id,map,mode,started_at,season}`,
`stats{puuid,agent,score,kills,deaths,assists,shots{head,body,leg},
damage{made,received},tier}`, `teams{red,blue}`. **Sem rounds/kills.**
→ Backfill de histórico em massa (barato), mas só métricas de resumo.

### 2.6 `/v2/matches/{matchid}` (ou v4 by id)
Uma partida completa (rounds+kills) por id. → Backfill **profundo** de
partidas antigas, 1 chamada por partida (caro, ver §5.3).

---

## 3. Catálogo completo de métricas deriváveis

Legenda de fonte: **R**=resumo (stats por partida) · **K**=kill events ·
**Rd**=round stats · **E**=economia · **P**=posições x/y · **M**=mmr.

### 3.1 Combate individual
| Métrica | Fonte | Cálculo |
|---|---|---|
| K / D / A, KDA, K/D | R | direto |
| ACS | R | `score / rounds` |
| ADR | R/Rd | `damage_dealt / rounds` |
| HS% | R | `head / (head+body+leg)` |
| Δ Dano | R | `dealt − received` por round |
| Multikills (2K–5K) | Rd | rounds com `kills ≥ n` |
| KAST% | K/Rd | % de rounds com Kill, Assist, Survived ou Traded |
| Mira por arma | K | HS% e kills por `weapon.name` |
| Uso de utilidade | R | `ability_casts` por round; ult econ |

### 3.2 Entrada (opening duels)
Primeiro kill de cada round (menor `kill_time_in_round`):
- **First Blood rate** (você fez o opening kill) e **First Death rate**.
- **Opening duel W/L** e **% de rounds vencidos após FB** vs perdidos após FD.
- Entrada é o sinal nº1 de "como joga": agressivo e eficaz, ou morre cedo à toa.

### 3.3 Clutch (1vX) — *algoritmo concreto*
Para cada round: ordenar `kills` por `kill_time_in_round`; varrer a timeline
mantendo **contagem de vivos por time**. Quando o jogador-alvo fica **o último
vivo do seu time**, registrar o tamanho `1vX` (X = inimigos vivos naquele
instante) e se o round foi vencido. Saídas: clutches tentados/convertidos por
tamanho, **taxa de conversão de clutch**, "fator clutch".

### 3.4 Trade — *algoritmo concreto*
Um kill é **trade** se o inimigo que matou seu colega morre dentro de **~3–5s**
(`kill_time_in_round`, em ms; janela a calibrar contra o dump). Deriva:
- **Trade %** (suas mortes que foram vingadas) → entra no KAST.
- **Mortes sem trade** = morrer isolado: métrica central de "onde erra".
- **Você troca bem?** (% das mortes de colega que você vinga).

### 3.5 Economia
Classificar cada round pelo `loadout_value` do time: **eco / força / full-buy**
(faixas a calibrar; ~0–5k eco, 5–20k força, >20k full). Deriva:
- Win rate por tipo de compra; **pistol round** (rounds 0 e 12) WR.
- **Eco frags** (kills com loadout baixo) e "anti-eco" (perder round comprado).
- Disciplina econômica: salvar quando devia / comprar quando devia salvar.

### 3.6 Lado (ataque/defesa) — *inferência, verificar no dump*
Não vem pronto. Inferir: metades por `round.id` (0–11 / 12–23) e **time que
planta = atacante** naquele round. Deriva WR de ataque vs defesa, e por mapa.
→ Marcar como "validar contra o dump antes de codar".

### 3.7 Plant/defuse & pós-plant
Plants, defuses, **site preferido** (A/B/C), **WR pós-plant** (atacante),
**sucesso de retake** (defensor vence após plant). Tempo médio de plant.

### 3.8 Posicionamento / heatmaps
`victim_death_location` e `player_locations_on_kill` (x/y) sobre a imagem do mapa
(Valorant-API tem o `displayIcon` do mapa) → **heatmap de mortes** (onde morre
demais) e **de kills** (onde domina). Por mapa e por lado.

### 3.9 Por mapa / por agente
Todas as métricas acima fatiadas por `map.name` e `agent.name`: WR, ACS, KDA,
HS%, entrada, clutch — para responder "melhor/pior mapa", "melhor agente",
"esse agente nesse mapa".

### 3.10 Contexto de rank
`tier` de todos os 10 → desempenho **vs lobbies acima/abaixo** do seu rank;
curva de RR (`mmr-history`) e trajetória por ato (`by_season`).

### 3.11 **Grupo / sinergia (peça central)**
Base: quando **2+ amigos cadastrados** estão na mesma partida **e mesmo time**
(detectável por `puuid` e confirmável por `party_id`).
- **Combos**: para cada subconjunto observado (dupla/trio/4/5-stack), WR
  conjunto, KDA somado, rounds jogados juntos. (Gating duro por nº de jogos.)
- **Pares complementares**: WR de A+B juntos vs A e B separados.
- **Quem puxa**: contribuição de cada um (ACS/impacto) nas vitórias do grupo.
- **Composição de agentes**: quais comps o grupo roda que vencem mais;
  cobertura de funções (duelista/controlador/sentinela/iniciador).
- **Como o grupo joga bem**: comparar sessões vencedoras vs perdedoras nas
  dimensões de entrada, trade, economia, FB — o que muda quando ganham?

### 3.12 Motor de insights — "onde erram / onde acertam"
Regras que transformam métricas em frases acionáveis (sempre com amostra mínima):
- *Erra*: alta taxa de first-death, muitas mortes sem trade, KAST baixo, perde
  ecos compradas, clutch baixo, morre repetidamente na mesma região (heatmap).
- *Acerta*: entrada eficaz, conversão de clutch, trade alto, multikills,
  domínio de mapa/agente, anti-eco.
- Saída: top 3 forças + top 3 lacunas por jogador, e por grupo.

---

## 4. Arquitetura de dados

### 4.1 Três camadas de armazenamento (decisão fechada)
O limite real é o **Supabase free = 500MB**. Blob v3 cru ≈ 300–500KB/partida
(quase tudo URL de asset e nome repetido). Guardar cru e completo estoura em
meses; guardar só normalizado perde dados que **não dá pra re-buscar** depois
que a partida sai da API. Solução em três partes:

1. **Ingestão pelo v4** (canônico, mais enxuto).
2. **Raw enxuto + comprimido**: remover tudo derivável de UUID (URLs de asset,
   nomes repetidos), `gzip` em coluna `bytea`. ~400KB → **~30–40KB**.
   → 1000 partidas ≈ **40MB**. Preserva a capacidade de recomputar métricas
   novas no futuro sem re-buscar.
3. **Tabelas normalizadas** para query rápida: `matches`, `match_players`,
   `player_round_stats`, `kills`. Nomes/ícones resolvidos **na leitura** via o
   cache de assets da Valorant-API que já temos.

### 4.2 Chave de junção = `puuid` (correção de correção)
Nomes mudam e o endpoint lifetime já voltou `name: ''`. **Todo join é por
`puuid`** (já guardamos `puuid` em `users`). `riot_id_tag` vira só rótulo de
exibição.

### 4.3 Esboço de schema novo
```
users(id, username, password_hash, puuid UNIQUE, riot_id, tagline, region, consent)
matches(match_id PK, map, mode, queue, started_at, rounds, region, raw_gz BYTEA)
match_players(match_id, puuid, team, agent, won, score, kills, deaths, assists,
              head, body, leg, dmg_made, dmg_recv, loadout_avg, party_id,
              tier, PRIMARY KEY(match_id, puuid))
player_round_stats(match_id, round_id, puuid, kills, dmg, hs, loadout_value,
                   buy_type, weapon, alive_end, was_traded, PRIMARY KEY(...))
kills(match_id, round_id, order_in_round, time_in_round_ms, killer_puuid,
      victim_puuid, weapon, hs, is_first, is_trade, vx INT, vy INT)
agg_player(puuid, ...janela...)  -- cache de agregados (opcional)
```
Métricas pesadas (clutch/trade/FB) são pré-computadas na ingestão e gravadas em
`player_round_stats`/`kills` (flags `is_first`, `is_trade`, `was_traded`), para
o backend só **somar**, nunca reparsear blob em request.

### 4.4 Ingestão (mudança no poller)
Hoje o poller grava só resumo. Novo fluxo por partida nova:
1. Buscar v4 → 2. Para cada amigo na partida (por `puuid`), extrair resumo →
3. Computar round/kill derivados (clutch, trade, FB, buy_type) →
4. Gravar normalizado + `raw_gz`. Idempotente por `match_id`.
Continua respeitando 30 req/min (serialização que já existe).

---

## 5. Histórico: o que dá pra prometer

### 5.1 Daqui pra frente (rico)
Clutch/trade/economia/heatmap precisam de rounds+kills, que só vêm em
`v4/matches` (recentes ~5) e `matches/{id}`. Logo, **análise profunda é real a
partir da ingestão** — cresce a cada partida jogada.

### 5.2 Backfill de resumo (barato)
`lifetime/matches` paginado preenche K/D/WR/mapa/agente de centenas de partidas
antigas rapidamente (1 chamada por página de ~10). Bom pro topo do funil.

### 5.3 Backfill profundo (caro, opcional)
Re-hidratar rounds/kills de partidas antigas = 1 chamada por `match_id`.
100 partidas × ~1/jogador ≈ **30+ min** a 30 req/min. Job lento, sob demanda,
não no caminho crítico. **Não prometer histórico profundo no dia 1.**

---

## 6. Gating de amostragem (princípio de produto, não nota de rodapé)
Cada número de "melhor/pior/sinergia/onde erra" carrega:
- **N mínimo** para aparecer como insight (ex.: mapa ≥5 jogos, agente ≥3,
  dupla ≥4 jogos juntos, clutch 1vX ≥5 tentativas).
- **Selo de confiança** ("dados baixos" abaixo do piso; mostra o número mas não
  o vende como conclusão).
- Combos de grupo: limitados à **co-ocorrência observada** e gate duro por
  jogos compartilhados. "Dupla 100% WR em 2 jogos" **não** é insight.

---

## 7. Governança e ética
Passamos a guardar telemetria + posições dos **10** jogadores, inclusive
adversários. A própria `PESQUISA.MD` aponta que a Riot proíbe *scouting* não
consentido. Para um tool privado de hobby o risco é baixo, mas o plano define:
- **Análise exibida só para amigos cadastrados/consentidos** (`users.consent`).
- Dados de adversário usados de forma **agregada/efêmera** (contexto de rank,
  comparação), nunca um perfil navegável de quem não optou.
- Comando/rota de **opt-out** (apaga puuid e dados associados).
- `_api_dump/` no `.gitignore` (contém nomes/puuids reais).

---

## 8. Frontend / UX (novas telas)
- **Perfil do jogador v2**: abas Visão / Combate / Entrada&Clutch / Economia /
  Mapas / Agentes / Mira. Cards + selo de confiança.
- **Heatmaps por mapa** (mortes vs kills) sobre a imagem do mapa.
- **Sessões**: linha do tempo de partidas com RR (`mmr-history`) e o que mudou
  nas vitórias vs derrotas.
- **Grupo**: matriz de sinergia (duplas), ranking de combos, composições que
  vencem, "como o grupo joga bem".
- **Insights**: cartões em linguagem natural (top forças/lacunas).
- Visual mantém o tema HUD tático já feito; gráficos com lib leve (ex.:
  `recharts`) e heatmap em `<canvas>`.

---

## 9. Roadmap em fases (ordem de construção)
Catálogo exaustivo, **build incremental** — payoff visível cedo:

- **Fase 0 — Fundação de dados.** Migrar para v4, schema novo, chave por puuid,
  raw_gz, ingestão normalizada. Backfill de resumo via lifetime. *(sem UI nova)*
- **Fase 1 — Combate + Mapa + Agente + Economia.** Perfil v2 com o que já dá
  bom retorno visual e roda sobre resumo+rounds. Gating embutido.
- **Fase 2 — Entrada, Clutch, Trade, KAST.** Algoritmos de §3.2–3.4 na ingestão;
  telas de entrada/clutch e "mortes sem trade".
- **Fase 3 — Sinergia de grupo.** Combos, pares, composições, "como o grupo joga
  bem". (É a mais faminta por dados → depois de haver histórico.)
- **Fase 4 — Heatmaps + Motor de insights** em linguagem natural.
- **Fase 5 (opcional)** — backfill profundo por match-id; tendências de rank.

---

## 10. Riscos e decisões em aberto
- **Lado ataque/defesa**: inferência a validar no dump antes de codar (§3.6).
- **Janela de trade**: calibrar 3–5s com casos reais.
- **Faixas de eco/força/full**: calibrar `loadout_value` por economia do meta.
- **Crescimento do banco**: monitorar; se passar de ~300MB, podar `raw_gz` de
  partidas muito antigas mantendo o normalizado.
- **party_id vs mesmo time**: `party_id` confirma fila conjunta; mas amigos em
  parties separadas no mesmo time também contam como "jogaram juntos" — decidir
  se sinergia usa party_id estrito ou co-ocorrência no time (proposto:
  co-ocorrência no time, com party_id como reforço).
- **Custo de recompute**: ao adicionar métrica nova, reprocessar `raw_gz` de
  todas as partidas (offline, fora do request).

---

### Apêndice — endpoints e amostras
A exploração inicial salvou dumps reais (gitignored) em `_api_dump/`
(`account`, `mmr`, `mmr_history`, `matches_v3`, `matches_v4`, `lifetime`). As
estruturas v4 efetivamente usadas estão documentadas em [SDD.md](SDD.md) §3 e §5.
