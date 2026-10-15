# X9 v2: diagnóstico e plano de melhoria

> Feito em 2026-10-15 a partir da leitura de `bot/x9/` e `backend/app/` e de
> medições locais (suítes do bot e do backend verdes; contagem de idas ao banco
> por tool). Tempos marcados com **≈** são estimativas pela leitura do código. A
> Fase 0 instala a medição real, e cada fase seguinte é aprovada pelos números.

---

## 0. Status da execução (2026-10-15)

| Fase | Status | Onde |
|---|---|---|
| 0. Medir e consertar | ✅ cronômetro por resposta/voz/partida, `/x9 status`, B1 (trava + `claim_gossip`), B2 (retentativa guardando o texto, desiste em 3), B3, B4 (aviso de cota), B5 (cache), B6 | `bot/x9/telemetry.py`, `announcer.py`, `backend/app/db.py` |
| 1. Partida sem atraso | ✅ 1.1 a 1.8 | `bot/x9/live.py`, `announcer.py`, `backend/app/ingest.py`, `announce.py`, `poll.py` |
| 2. Resposta em 1–3 s | ✅ 2.1 a 2.7 (catálogo do LLM com 8 tools agrupadas) | `backend/app/botapi.py`, `bot/x9/llm.py`, `brain.py`, `streaming.py`, `discord_bot.py` |
| 3. Voz instantânea | ✅ 3.1 a 3.4 e 3.6; 3.5 é só configuração (`WHISPER_MODEL=large-v3-turbo`, `WHISPER_COMPUTE=int8_float16`) | `bot/x9/voice.py`, `stt.py` |
| 4. Insights | ✅ 4.1 a 4.4 | `backend/app/storylines.py`, `botapi.py`, `bot/x9/moments.py` |
| 5. Criatividade | ✅ 5.1 a 5.6; ❌ 5.7 (card de placar em imagem, opcional) ficou de fora | `bot/x9/brain.py`, `announcer.py`, `discord_bot.py` |
| 6. Infraestrutura | ⏸️ decisão sua (conta na Oracle, pedido de chave à HenrikDev, migração do banco). Com chave maior, baixe `LIVE_MIN_GAP_S`. A 6.4 (cliente da Riot) não foi feita por ser API não oficial | — |

**Validado aqui**: suítes do backend (70) e do bot (100) verdes em SQLite **e**
Postgres 16, com testes novos para cada item acima. **Não validado aqui** (sem
as chaves no container): chamadas reais ao Discord, Gemini, Groq e HenrikDev,
Whisper e TTS. O streaming foi testado contra respostas simuladas no formato
documentado de cada provedor. Na primeira subida, confira `/x9 status` e o
`x9.log`.

---

## 1. Resumo

O X9 funciona, mas tudo nele acontece **em fila e às cegas**. Três causas
explicam quase todo o atraso e a falta de graça:

1. **Tudo em cadeia.** Para cada partida, o bot busca o RR, depois posta o
   placar, depois roda o LLM em 3 passos para a fofoca e depois roda **outra**
   cadeia de LLM para a voz. Para cada pergunta, são 2 a 4 idas ao LLM, e cada
   uma espera tools que fazem de 4 a 9 idas a um banco que fica a ≈170 ms
   (Brasil → Supabase em Oregon).
2. **O LLM busca o contexto em vez de recebê-lo.** O X9 começa toda conversa
   sem saber nem qual foi a última partida e gasta passos de tool para
   descobrir o óbvio. É o que mais custa tempo e também a cota do Groq
   (8 mil tokens por minuto no plano grátis).
3. **Pouco material para ser criativo.** A fofoca vê só o placar e os momentos
   da partida. Não sabe que foi a pior partida do fulano no mês, a 4ª derrota
   seguida na Bind ou que ele caiu de elo. O pedido é sempre o mesmo ("quem
   carregou, quem afundou, 8 linhas"), então o texto sai sempre igual.

| Indicador | Hoje (≈) | Meta |
|---|---|---|
| Fim da partida → placar no Discord (bot ligado) | 2–8 min | HenrikDev + 30 s (≈ 1,5–3 min) |
| Fim da partida → placar (PC desligado) | 15–20 min | ≤ 5 min (com a Fase 6: igual ao bot ligado) |
| Placar → fofoca na thread | 10–25 s (até +2 min se o placar veio do poller) | ≤ 8 s |
| Pergunta de texto → resposta | 5–12 s (45 s+ quando bate a cota) | p50 ≤ 2,5 s, p95 ≤ 6 s, 1ª frase ≤ 1,5 s |
| Voz: fim da fala → início da resposta | 6–15 s (disfarçado pelo "Deixa eu ver.") | ≤ 3 s |
| Fofoca com a mesma estrutura | sempre | nunca o mesmo formato em 3 partidas seguidas |
| Perguntas sem resposta (RR, noite de hoje, pré-fila, armas, sinergia, "por que perdemos a partida"…) | ~8 tipos | 0 |

**A parte que não dá para encurtar:** nem a Riot nem a HenrikDev publicam a
partida ao vivo. Ela só aparece de 1 a 3 min depois do fim. A meta de "sem
atraso" é chegar a **segundos depois disso**. A única exceção é a opção 6.4
(cliente da Riot).

---

## 2. Diagnóstico

### 2.1 Linha do tempo de uma partida, hoje

```
fim da partida
  │ 1–3 min  HenrikDev publica (fora do nosso controle)
  │ 0–2 min  próxima checagem do ao-vivo (0–5 min se a atividade não é compartilhada)
  │ ≈3–8 s   /v4/matches baixa as 5 últimas partidas COMPLETAS (payload grande)
  │ ≈5 s     mmr-history + mmr: 2 requisições com throttle de 2,1 s, ANTES do placar
  │ ≈1,5 s   build_embed: 8 idas ao banco
  ▼ placar no canal
  │ ≈6–15 s  fofoca: LLM → tools (12 idas ao banco) → LLM → tools → LLM
  ▼ fofoca na thread
  │ ≈4–8 s   voice_line: OUTRA cadeia LLM → resumo_partida → LLM
  ▼ voz na call
```

Com o PC desligado, a ordem é: pg_cron (15 min) → o GitHub Actions sobe
(≈1 min) → ingestão de todos → MMR → **deep backfill de 10 partidas (≈25 s+)**
→ só então `announce.run()`. A fofoca só sai quando o PC liga.

| # | Onde | Problema |
|---|---|---|
| L1 | `bot/x9/live.py:131` | RR e MMR (2 requisições) são buscados **antes** de devolver a partida, e o placar espera. |
| L2 | `bot/x9/live.py:106` | Só o jogador checado sai da rajada. Os outros 4 amigos da mesma partida continuam checando (≈16 requisições jogadas fora por stack de 5, disputando a cota de 30/min). |
| L3 | `bot/x9/live.py` | Se o VALORANT não publica `details/state` no Discord, a "mudança de estado" nunca dispara e o fim da partida só aparece na checagem de 2 min. **Conferir no `x9.log`** se existem linhas `Ao vivo: X → …`. |
| L4 | `backend/app/ingest.py:535` | O poller só anuncia depois do MMR de todos e do deep backfill. |
| L5 | `bot/x9/announcer.py:40` | Quando o placar vem do poller, a fofoca espera o tick de 2 min (`GOSSIP_POLL_S`), mas a mensagem do webhook chega ao bot na hora, pelo gateway. |
| L6 | `backend/app/valorant_api.py` | Cada checagem baixa 5 partidas completas, sendo que só a mais nova interessa. |

### 2.2 Anatomia de uma pergunta, hoje

`@X9 quem carregou na última?`

1. `linked_label`: 3 idas ao banco (≈0,5 s).
2. LLM, passo 1. Leva ≈1,8 mil tokens fixos (persona com 1,2 mil caracteres
   mais o catálogo de 13 tools com 5,4 mil caracteres) e pede `resumo_partida`.
3. `resumo_partida`: 6 idas ao banco (≈1 s).
4. LLM, passo 2: muitas vezes pede também `momentos_chave` (mais 6 idas).
5. LLM, passo 3: escreve o texto.
6. Só então a mensagem inteira é enviada, sem streaming.

**Total: ≈5–12 s.** No Groq, cada passo reserva ≈2,6 mil dos 8 mil tokens por
minuto. Três passos de uma pergunta já estouram o minuto, e o
`FallbackProvider` espera até 45 s em silêncio (`bot/x9/llm.py:300`).

Idas ao banco por tool (medido nos testes, SQLite). Em produção, cada ida
custa ≈150–200 ms:

| Tool | Idas | Tool | Idas |
|---|---|---|---|
| `resumo_partida` | 6 | `desempenho_jogador` | 5 |
| `momentos_chave` | 6 | `tendencia_jogador` | 4 |
| `partidas_recentes` | 6 | `mapas_e_agentes` | 5 |
| `narrar_round` | 6 | `comparar_jogadores` | 9 |
| `analisar_round` | 6 | `ranking_grupo` | 5 |
| `build_embed` (placar) | 8 | `linked_label` (toda mensagem) | 3 |

Boa parte disso é repetição. O `_Ctx` recarrega o roster e os vínculos em toda
tool (`backend/app/botapi.py:777`), `_Match.header` busca de novo a linha da
partida e o raw é descomprimido de novo a cada tool, embora uma partida nunca
mude.

### 2.3 Voz

- O fim da fala só é detectado depois de 0,8 s de silêncio (`bot/x9/voice.py:22`).
- O Whisper `small` leva ≈1 s na GPU e ≈2 s na CPU.
- A resposta passa pela mesma cadeia de LLM e tools do texto.
- O TTS sintetiza a resposta **inteira** antes de tocar a primeira sílaba.
- O aviso é sempre o mesmo "Deixa eu ver.".

### 2.4 Criatividade

- O pedido de fofoca é fixo (`bot/x9/brain.py:141`): mesmo pedido e mesma forma
  em toda partida.
- Não há memória entre partidas. O X9 não lembra da zoeira de ontem, não cria
  apelidos e não faz callback.
- O material é pobre. A fofoca enxerga a partida isolada, sem a média do
  jogador, sem o histórico e sem o RR.
- Há uma persona só, sem um modo "coach" sério para quando o grupo quer
  melhorar.
- Temperatura 0,9 sem material novo não gera variedade, gera sinônimos.

### 2.5 Insights: o backend sabe, o X9 não alcança

O site já calcula tudo isto, mas nenhuma tool do X9 chega lá:

| Já pronto no backend | Onde | Pergunta que o X9 não responde hoje |
|---|---|---|
| Curva de RR, elo atual e pico | `progress.rank_curve`, `db.rank_profile_for_puuid` | "Quanto de RR eu perdi hoje?", "Qual o elo do fulano?" |
| Sessões e tilt | `progress.sessions` | "Como foi a noite?", "Vale jogar mais uma?" |
| Pré-fila (mapas e escalação) | `squad.pre_queue` | "Qual mapa a gente aceita?", "Quem joga de quê?" |
| Sinergia e stacks | `squad.synergy_matrix`, `analytics.squads` | "Qual a melhor dupla do grupo?" |
| HS% e K/D por arma | `analytics.player_weapons` | "Como tá minha Vandal?" |
| Pós-plant, retake e utilidade | `analytics.player_postplant`, `player_utility` | "A gente segura pós-plant?" |
| Você vs lobby e vs seu elo | `matchview.vs_lobby` | "Eu jogo acima do meu elo?" |
| Metas de treino | `progress.evaluate_goals` | "Bati minha meta?" |

Também **não existe em lugar nenhum**:

- "Por que perdemos a **partida**" (hoje só existe a análise por round).
- A comparação de cada jogador com a **própria média**.
- Recordes pessoais e marcos do grupo.

### 2.6 Bugs encontrados

| # | Onde | Bug |
|---|---|---|
| B1 | `bot/x9/announcer.py:40` e `:90` | **Fofoca duplicada.** `announce_now` (ao-vivo) e o tick de 2 min chamam `gossip_pending()` ao mesmo tempo, sem trava. Os dois leem o mesmo pendente e os dois postam. |
| B2 | `bot/x9/announcer.py:94-123` | **Fofoca travada.** Se o post na thread falhar (permissão, thread arquivada, rede), a exceção sai do laço. A partida fica pendente, o LLM é pago de novo a cada 2 min e as partidas seguintes da fila não saem. |
| B3 | `bot/x9/brain.py:158` | A voz paga o LLM duas vezes por partida: `voice_line` refaz a cadeia que a fofoca acabou de fazer. |
| B4 | `bot/x9/llm.py:300` | A espera de cota pode chegar a 45 s sem nenhum aviso ao usuário. |
| B5 | `backend/app/botapi.py:777` e `:805` | O roster e os vínculos são relidos em toda tool, e `linked_label` faz 3 idas ao banco em toda mensagem. |
| B6 | `bot/tests/test_wake_audio.py` | Dois testes falham quando `av` ou `edge_tts` não estão instalados. Deveriam usar `pytest.importorskip`. |

---

## 3. Plano

Cada fase é entregue e medida separadamente. As Fases 0 a 3 resolvem o
**atraso**, e as Fases 4 e 5 resolvem **insights e criatividade**.

### Fase 0: medir e consertar (½ a 1 dia)

- **0.1 Cronômetro por etapa.** Log estruturado por resposta (`llm_ms`,
  `tools_ms`, `db_ms`, passos, provedor, tokens) e por partida (fim da partida
  → ingerida → placar → fofoca → voz). Guardar `ended_raw` (início mais
  `game_length_in_ms` da v4) e `posted_raw` em `bot_announcements`.
- **0.2 `/x9 status`.** Mostra p50 e p95 das últimas 20 respostas e partidas,
  a saúde dos provedores (castigo e cota), o estado do ao-vivo (quem está sendo
  checado e quando) e a última execução do poller.
- **0.3 B1.** `asyncio.Lock` em `gossip_pending` e claim atômico no banco
  (`UPDATE … SET gossip_raw = -1 WHERE gossip_raw IS NULL`), o que protege até
  de duas instâncias do bot.
- **0.4 B2.** `try/except` por partida, contador de tentativas (depois de 3,
  posta solto no canal) e cache do texto já gerado, para não pagar o LLM de
  novo.
- **0.5 B6.**

**Pronto quando:** o `/x9 status` mostra números reais e um teste de
concorrência prova que sai 1 fofoca por partida.

### Fase 1: partida sem atraso (1 a 2 dias)

- **1.1 Placar antes do RR** (L1). A partida é anunciada assim que é gravada. RR
  e MMR rodam depois, em segundo plano, e **editam o embed** com algo como
  "+18 RR · subiu pra Ouro 3".
- **1.2 Partida achada tira todos os amigos dela da rajada** (L2). As
  requisições economizadas viram checagens mais frequentes.
- **1.3 Checagem leve** (L6). Pedir só a partida mais nova
  (`/v4/matches?size=1`; confirmar o parâmetro na doc da v4) e baixar apenas o
  que ainda não existe.
- **1.4 Agenda mais curta com a mesma cota.** Jogando: a cada 60 s. Rajada
  depois do fim: +30 s, 60 s, 90 s, 2 min, 3 min e 5 min (a HenrikDev costuma
  publicar em 1 a 3 min). O teto continua em 12 req/min.
- **1.5 Fim da partida pela presença** (L3). Se o Discord trouxer
  `details`/`state`/`party`, a transição "em partida → menu" vira o gatilho. A
  checagem é feita por **party** (o `activity.party.id` junta o stack), com um
  jogador checado por partida em vez de cinco.
- **1.6 Fofoca instantânea para o placar do poller** (L5). O bot escuta
  `on_message` no canal do placar: mensagem com `webhook_id` do X9 dispara a
  fofoca na hora. O tick de 2 min vira só rede de segurança.
- **1.7 Poller mais esperto** (L4). Anuncia logo depois de gravar as partidas
  novas, antes do MMR e do deep backfill. Pula quem o bot checou há menos de
  3 min (`ingest_state.last_ingested_raw`), para não dividir a cota à toa.
- **1.8 Fofoca em paralelo com o placar.** Começa a ser gerada junto com o post
  do embed, não depois.

**Pronto quando:** p50 de fim da partida → placar ≤ HenrikDev + 30 s, e placar
→ fofoca ≤ 8 s, medidos pela Fase 0.

### Fase 2: resposta em 1 a 3 s (2 a 3 dias)

- **2.1 Cache no bot, em memória.**
  - Roster e vínculos: TTL de 60 s, invalidado no `/x9 vincular`.
  - Raw da partida: LRU de 64, porque a partida é imutável.
  - `player_overview`, `insights` e `trend` por jogador, com chave igual à
    versão dos dados (a última partida gravada).
  - Meta: no caso quente, tool com no máximo 1 ida ao banco, e a maioria com
    nenhuma.
- **2.2 Contexto pronto no prompt (briefing).** É o maior ganho de todos. Um
  resumo compacto (≈600 tokens) fica em memória e é reconstruído quando entra
  partida nova. Ele traz:
  - roster e vínculos;
  - as últimas 5 partidas do grupo, com a linha de cada amigo;
  - os fatos da última partida (Fase 4);
  - a noite de hoje;
  - elo e RR de cada um;
  - quem está jogando agora.

  As perguntas comuns ("Quem carregou?", "Como foi hoje?", "Qual o elo do X?")
  passam a sair com **1 chamada de LLM e zero tools**. As tools ficam para o
  que é fundo: round, comparação e histórico longo.
- **2.3 Streaming.** Gemini com `streamGenerateContent?alt=sse` e Groq com
  `stream: true`. A primeira frase sai assim que existe e a mensagem é editada
  conforme o resto chega (no máximo ≈1 edição por segundo, por causa do limite
  do Discord). Na voz, cada frase pronta já vai para o TTS (Fase 3).
- **2.4 Prompt mais enxuto.**
  - Descrições de tool mais curtas.
  - Tools parecidas juntas (ex.: `narrar_round` e `analisar_round` viram uma só,
    com `modo`).
  - Resultados de tool sem os campos que o LLM não usa.

  Menos tokens por passo deixa a resposta mais rápida e faz caber no TPM do Groq.
- **2.5 Roteamento por tarefa.**
  - Voz: Groq (`gpt-oss-20b`), que tem a menor latência.
  - Texto: Gemini Flash.
  - Fofoca: a configuração mais criativa. Pode até "pensar" um pouco, porque
    sai em paralelo com o placar.

  Cada rota tem o seu fallback.
- **2.6 Nada de espera muda.** Se o provedor pedir mais de 5 s, a pergunta cai
  direto no próximo provedor (ou no modelo pequeno). Quando não houver
  alternativa e for preciso esperar, o X9 avisa ("tô no limite da cota, já
  respondo").
- **2.7 Atalhos sem LLM**, como o `/x9 ultima` que já existe: `/x9 hoje`,
  `/x9 elo`, `/x9 ranking` e `/x9 fila` (pré-fila). São instantâneos e deixam o
  LLM só para conversa.

**Pronto quando:** texto com p50 ≤ 2,5 s e p95 ≤ 6 s, e 60% ou mais das
perguntas respondidas sem tool.

### Fase 3: voz instantânea (1 a 2 dias)

- **3.1 Pipeline por frase.** O LLM gera em streaming, a 1ª frase vai para o TTS
  e toca. As frases seguintes são sintetizadas enquanto a 1ª toca.
- **3.2 Ouvir mais rápido.**
  - Fim da fala em 0,5 a 0,6 s (hoje são 0,8 s).
  - Wake word antecipado: se a fala começa com "X9", o aviso já toca e o
    contexto já é preparado enquanto a pessoa termina de falar.
- **3.3 Avisos variados.** Um pool de ≈12 respostas rápidas pré-sintetizadas no
  warmup ("Pera que eu vou puxar a ficha…", "Hmm, isso vai doer…"), sorteadas
  sem repetir.
- **3.4 Barge-in.** Se alguém disser "X9" enquanto ele fala, ele para e escuta.
- **3.5 Opcional: Whisper `large-v3-turbo` na GPU** (`int8_float16`), que acerta
  melhor nomes e gíria com latência parecida. Testar na GTX 1650 Super e manter
  o `small` na CPU.
- **3.6 Fofoca falada na mesma geração do texto.** Um bloco "VOZ:" no fim da
  resposta, sem uma 2ª cadeia de LLM (resolve o B3).

**Pronto quando:** são no máximo 3 s do fim da fala até o começo da resposta,
sem contar o aviso.

### Fase 4: insights de verdade (3 a 4 dias)

**4.1 Motor de fatos** (`backend/app/storylines.py`, sem LLM e testável com as
fixtures v4). Para cada partida, ele gera ganchos e dá a cada um uma nota de
**surpresa** (raridade × tamanho). Exemplos:

| Tipo de gancho | Exemplo |
|---|---|
| Contra a própria média | "pior ACS do fulano em 30 partidas" (z-score das últimas 20) |
| Recordes e marcos | maior ACS da história, primeiro ace, 100ª partida juntos |
| Sequências | 4ª derrota seguida na Bind, 5 vitórias seguidas do stack |
| RR e elo | +/− RR, subiu ou caiu de elo, "a 7 RR do Platina" |
| Noite (tilt) | "6ª da noite; o grupo cai de 58% para 31% depois da 3ª" |
| Abertura | "tomaram first blood em 15 de 24 rounds e ganharam 3 desses" |
| Trades | "morreu 9 vezes sem ninguém vingar" |
| Economia | "perderam 4 de 5 full buys", "roubaram 3 ecos" |
| Lobby e elo | "último do lobby", "acima da média do próprio tier" |
| Agente e mapa | estreia de agente, mapa que a pré-fila mandava evitar |

O LLM recebe os 5 a 7 melhores ganchos, cada um com o número que o prova, e
escolhe o ângulo. **É isso que transforma "fulano foi mal com 8/15" em
zoeira com contexto.**

**4.2 Tool `analisar_partida`** ("Por que perdemos?"). Ranqueia por impacto os
fatores da partida inteira:

- aberturas;
- trades;
- conversão de economia;
- pós-plant e retake;
- ataque vs defesa;
- pistols e o round seguinte;
- quem ficou abaixo da própria média.

**4.3 Expor o que já existe:** `elo_e_rr`, `sessao_hoje`, `pre_fila`,
`sinergia_grupo`, `armas_jogador`, `vs_lobby`, `metas` e `recordes` (seção
2.5).

**4.4 Momentos proativos**, sem ninguém perguntar. Cada um tem um liga/desliga.

- **Pré-fila:** quando 2 ou mais do grupo abrem o VALORANT juntos, pela
  presença, o X9 posta os mapas para aceitar e evitar e a escalação.
- **Fim da noite:** quando todos fecham o jogo, ele posta o resumo: saldo, MVP
  da noite, quem tiltou e o RR total.
- **Subiu ou caiu de elo:** anúncio na hora.
- **Domingo à noite:** "Oscar do X9" da semana, com MVP, bagre, mais tilt,
  melhor dupla e round da semana.

**Pronto quando:** toda fofoca cita ao menos 1 fato de contexto (histórico, RR
ou noite) e as perguntas da seção 2.5 são respondidas com número.

### Fase 5: criatividade (2 a 4 dias)

- **5.1 Formatos sorteados para a fofoca,** sem repetir os 3 últimos. Cada
  formato é 1 linha de instrução. Exemplos:
  - plantão urgente;
  - narração de futebol;
  - boletim de ocorrência;
  - laudo médico;
  - previsão do tempo;
  - revista de fofoca;
  - review com estrelas;
  - post motivacional de LinkedIn;
  - trailer de filme;
  - luta de UFC;
  - relatório de RH;
  - sessão de terapia.
- **5.2 Memória anti-repetição.** Guarda as últimas ≈15 fofocas e manda no
  prompt "não repita estas aberturas e piadas: …".
- **5.3 Lore persistente** (tabela `bot_lore`, com validade). Guarda três
  coisas:
  - apelidos conquistados pelos dados ("Rei do first death" depois de 3
    partidas liderando);
  - momentos memoráveis (o ace, a pior partida);
  - piadas internas.

  Com isso o X9 faz callback ("de novo, igual à Bind de terça"). O `/x9 lore`
  mostra o que está guardado e o admin pode apagar.
- **5.4 Modos por canal** (`/x9 modo`): `zoeira` (padrão), `coach` (sério, com
  foco em melhorar) e `narrador`.
- **5.5 Interação.**
  - Reação automática no placar (👑 💀 🔥 🧊).
  - Votação de MVP com botões.
  - `/x9 aposta` na próxima partida, com um placar de apostas que o X9 fecha
    sozinho.
- **5.6 Temperatura por tarefa.** Consulta e fatos com 0,6 a 0,7; fofoca com 1,0
  a 1,1. Com o material da Fase 4, mais temperatura vira criatividade, não
  invenção.
- **5.7 Opcional: card de placar em imagem** (Pillow e ícones da Valorant-API)
  no lugar do embed de texto.

**Pronto quando:** o mesmo formato nunca aparece em 3 partidas seguidas, e a
votação e as apostas funcionam.

### Fase 6: infraestrutura (opcional, decisão sua)

| # | Opção | Ganho | Custo e risco |
|---|---|---|---|
| 6.1 | **Bot 24/7 numa VM grátis** (ex.: Oracle Cloud Always Free, ARM) | Acaba o "PC desligado": placar e fofoca em 1,5–3 min, sempre. O poller do GitHub vira só reserva. | Conta na Oracle (pede cartão) e a oferta de ARM varia por região. Whisper em CPU ARM fica em ≈2–3 s. |
| 6.2 | **Chave Advanced da HenrikDev** (pedida no Discord deles; conferir os limites atuais) | Cerca de 3× mais cota, o que permite checar a cada 30 s. | Depende de aprovação. |
| 6.3 | **Supabase em São Paulo** (`sa-east-1`) | Cada ida do bot ao banco cai de ≈170 ms para ≈20 ms. | Exige migrar o banco, e a API no Render (Oregon) fica mais lenta. Com o cache da Fase 2, quase não é necessário. |
| 6.4 | **Presença pelo cliente da Riot** no PC do bot (API local) | Sabe **na hora** que a partida acabou e vê até o placar ao vivo ("tá 9x3 na Ascent") de todos os amigos da conta. | API local não oficial (área cinzenta dos termos da Riot) e só funciona com o cliente aberto nesse PC. |

---

## 4. Ordem recomendada

| Semana | Entrega | O que o grupo sente |
|---|---|---|
| 1 | Fases 0, 1 e 2.1–2.2 | Placar logo depois da HenrikDev, fofoca colada no placar, respostas em poucos segundos, sem fofoca duplicada |
| 2 | Fases 2.3–2.7, 3 e 4.1–4.3 | Resposta "digitando" na hora, voz quase instantânea, fofoca com contexto e as perguntas novas respondidas |
| 3 | Fases 4.4 e 5 | X9 proativo (pré-fila, fim da noite, Oscar), formatos variados, apelidos e callbacks, votação e apostas |

Total estimado: 10 a 16 dias de trabalho. A Fase 6 entra quando você decidir.

## 5. Como validar

- **Testes novos.**
  - Concorrência da fofoca (B1) e falha de post (B2).
  - Motor de fatos e `analisar_partida` com as fixtures v4.
  - Briefing.
  - Streaming com provedor falso.
  - Rotação de formatos sem repetição.
  - Rajada do ao-vivo encerrada para o stack inteiro.
- **Produção.** O `/x9 status` mede p50 e p95 antes e depois de cada fase. Uma
  fase só conta como entregue quando bate a meta da tabela da seção 1.

## 6. Riscos

| Risco | Mitigação |
|---|---|
| Mais proatividade = mais mensagens no canal | Liga/desliga por env e por comando; a pré-fila e o fim da noite saem uma vez por noite. |
| Apelidos e lore que passam do ponto | Mesma regra do prompt (só desempenho no jogo), com validade e `/x9 lore apagar`. |
| Cota de LLM | O briefing corta passos e a fofoca usa 1 chamada. O consumo aparece no `/x9 status`. |
| Limite de edição de mensagem no Discord | Streaming com no máximo ≈1 edição por segundo. |
| Privacidade dos adversários | Fatos novos seguem a regra atual: quem não é do grupo aparece só pelo agente. |
