# PDD — Documento de Design de Produto

**Produto:** VavaPPRT — Plataforma de Análise de Desempenho para Valorant
**Público:** grupos fechados de amigos (≤10) que jogam competitivo juntos
**Versão:** 2.x · **Atualizado:** 2026-06

---

## 1. Problema

Jogadores de Valorant que jogam em grupo querem melhorar, mas as ferramentas
existentes (tracker.gg etc.) são individuais, genéricas e não respondem às
perguntas que um grupo de amigos realmente faz no dia a dia:

- Onde **eu** erro e acerto? (entro mal? morro isolado? perco eco?)
- Quais **mapas** o nosso grupo joga bem e quais evitar?
- Quais **combinações de pessoas** rendem juntas — e quais não?
- Quem **puxa o grupo pra cima** e quem rende mais sozinho?

A API oficial da Riot é inviável para um projeto pessoal (exige Production Key +
RSO/OAuth2 — ver [PESQUISA.MD](PESQUISA.MD)). A solução usa a API comunitária
HenrikDev.

## 2. Visão

> Uma plataforma privada do grupo que transforma telemetria bruta em **decisões
> acionáveis do dia a dia**: o que treinar, o que filar, com quem jogar.

Não é mais um placar. É um **co-piloto de melhoria** para o squad.

## 3. Personas

- **O capitão informal** — organiza as filas. Quer saber *com quem* e *em qual
  mapa* o grupo deve jogar hoje. Usa a aba **Grupo**.
- **O que quer subir de elo** — quer saber *o que está travando* o desempenho
  dele. Usa **Perfil → Insights/Combate/Heatmap**.
- **O curioso/competitivo** — gosta de comparar. Usa **Ranking** e perfis dos
  amigos.

## 4. Princípios de produto

1. **Acionável > exaustivo.** Cada número vem com um "e daí?" — recomendação,
   comparação ou contexto. O motor de insights e a recomendação de grupo existem
   para isso.
2. **Nunca vender ruído como insight.** É um grupo de amigos, não um dataset
   profissional. Todo recorte (mapa, agente, dupla, clutch) tem **piso de amostra**
   e **selo de confiança** ("dados baixos" abaixo do piso). "Dupla 100% em 2 jogos"
   não é insight.
3. **Privacidade primeiro.** Só se exibe análise de quem está no grupo
   (consentido). Adversário só entra agregado.
4. **Zero atrito de manutenção.** Dados se atualizam sozinhos (cron). Adicionar
   uma pessoa baixa o histórico e forma grupos automaticamente.

## 5. Funcionalidades (o que o produto entrega)

### Perfil individual (abas)
| Aba | Responde |
|---|---|
| Visão | Como vou no geral? (WR, K/D, ACS, ADR, HS%, KAST, últimas partidas) |
| Combate | Entro bem? Faço clutch? Morro isolado? (FB/FD, clutch 1vX, trade, multikills) |
| Economia | Ganho pistol/eco? Sou melhor no ataque ou defesa? |
| Mapas | Meus melhores e piores mapas (com confiança) |
| Agentes | Com quem jogo melhor/pior (forte/fraco) |
| Armas | No que eu frageio |
| Insights | Top forças e lacunas, em texto |
| Heatmap | Onde eu morro demais / domino, por mapa |

### Grupo
- **Adicionar pessoa** (Riot ID#Tag) → baixa dados → forma grupos.
- **Lista de grupos (2–5)** que jogam juntos, com WR e confiança.
- **Detalhe do grupo**: recomendação em texto, forma recente (V/D), **lift por
  membro** (juntos vs sozinho), mapas pra filar/evitar, composições, e
  **métricas profundas** (FB/clutch/KAST/trade) das partidas completas juntos.

### Ranking
- Tabela do grupo (WR, K/D, KAST, ACS) — bragging rights e comparação.

## 6. Fluxos principais

**Onboarding:** registro (usuário/senha + Riot ID) → backfill de histórico →
perfil populado.

**Adicionar amigo:** aba Grupo → formulário → validação na Henrik → dados
baixados em segundo plano (~30s) → grupos formados.

**Decisão de fila:** aba Grupo → clica no grupo → lê recomendação ("filem Lotus,
evitem Bind, fulano rende mais solo").

## 7. Métricas de sucesso

- **Adoção:** todos os amigos do grupo cadastrados e com histórico.
- **Engajamento:** uso antes/depois de sessões de fila.
- **Confiança:** os insights batem com a percepção dos jogadores (validação
  qualitativa) — sem isso o produto perde credibilidade.

## 8. Não-objetivos (escopo deliberadamente fora)

- Não é ferramenta de *scouting* de adversários.
- Não é público/aberto — é do grupo.
- Não substitui VOD review; complementa com dados.
- Sem mobile app nativo (web responsiva basta).

## 9. Roadmap

| Fase | Entrega | Status |
|---|---|---|
| F0 | Fundação de dados (v4, puuid, storage) | ✅ |
| F1 | Combate, mapas, agentes, economia | ✅ |
| F2 | Entrada, clutch, trade, KAST | ✅ |
| F3 | Grupos, sinergia, lift, recomendação | ✅ |
| F4 | Heatmaps + motor de insights | ✅ |
| F5 | Backfill profundo (clutch/economia retroativos) | ⏳ |
| — | Tendência de rank/RR no tempo | ⏳ |

## 10. Riscos de produto

- **Amostra pequena** num grupo de amigos → mitigado por gating/confiança.
- **Dependência da HenrikDev** (API comunitária) → desenho resiliente a falhas e
  rate limit; dados servidos do banco.
- **Heatmap** depende de transform de coordenadas a calibrar visualmente.
