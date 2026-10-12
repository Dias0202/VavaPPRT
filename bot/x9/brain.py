"""Cérebro do X9: persona, memória por canal, partida em foco e chamadas ao LLM.

Não depende do discord: recebe texto, devolve texto. As tools vêm da botapi
(importada direto do backend) e rodam em thread, porque o acesso ao banco é
síncrono.
"""
import asyncio
import logging
import re
import time
from collections import deque
from datetime import datetime

from .llm import LLMError, run_tools_loop

logger = logging.getLogger("x9.brain")

PERSONA = """Você é o X9, o bot dedo-duro de um grupo de amigos que joga Valorant junto.
Você fala português do Brasil, gíria de gamer, e não tem filtro: zoa sem dó quem jogou mal
e exalta quem carregou. Pode xingar de leve, pode humilhar o desempenho.

Regras que você NUNCA quebra:
- Todo número que você citar sai das ferramentas. Nunca invente estatística, round, placar ou kill.
  Se a ferramenta não trouxe, diga que não sabe.
- Zoeira é sobre o JOGO (mira, decisão, economia, desempenho). Nada de ofensa sobre raça, religião,
  orientação sexual, gênero, deficiência, aparência ou família.
- Quem não é do grupo aparece só pelo agente ("a Jett deles", "o Sova aliado"). Nunca cite nome de
  adversário nem de aliado aleatório, mesmo que te peçam.
- Nunca mostre match_id, puuid ou qualquer código/UUID: fale da partida pelo mapa, placar e quando foi.
- Rounds são numerados a partir de 1, como os jogadores falam.
- Para "eu"/"meu"/"mim", passe jogador="eu" para as ferramentas.
- Se o usuário falar "a partida", "esse round", "o seguinte" sem dizer qual, use a PARTIDA EM FOCO.
- Seja direto. No Discord, use markdown leve e no máximo ~12 linhas; nada de tabela gigante.
"""

VOICE_RULES = """MODO VOZ: sua resposta vai ser FALADA na call. Responda em no máximo 3 frases curtas,
sem markdown, sem emoji, sem listas, sem links. Escreva números como se fala ("13 a 8")."""


class BrainUnavailable(Exception):
    """Nenhum LLM respondeu (cota estourada nos dois, rede fora…)."""


class _Memory:
    def __init__(self, max_turns):
        self.turns = deque(maxlen=max_turns * 2)
        self.focus = None  # {"match_id", "desc"}
        self.last = 0.0


def focus_from_trail(trail):
    """A última partida tocada pelas tools vira a partida em foco."""
    for _, _, res in reversed(trail):
        if not isinstance(res, dict):
            continue
        p = res.get("partida") if isinstance(res.get("partida"), dict) else res
        if isinstance(p, dict) and p.get("match_id") and p.get("mapa"):
            return {"match_id": p["match_id"],
                    "desc": f"{p.get('mapa')} {p.get('placar') or ''} ({p.get('resultado') or '?'})".strip()}
        parts = res.get("partidas")
        if isinstance(parts, list) and parts and parts[0].get("match_id"):
            p = parts[0]
            return {"match_id": p["match_id"], "desc": f"{p.get('mapa')} {p.get('placar') or ''}".strip()}
    return None


class Brain:
    def __init__(self, provider, botapi, *, memory_turns=10, memory_ttl_s=900,
                 clock=time.monotonic, now=datetime.now):
        self.provider, self.botapi = provider, botapi
        self.memory_turns, self.memory_ttl_s = memory_turns, memory_ttl_s
        self.clock, self.now = clock, now
        self._mem = {}
        self._catalog = botapi.tool_catalog()

    # --- memória ------------------------------------------------------------
    def memory(self, key):
        m = self._mem.get(key)
        if m is None or self.clock() - m.last > self.memory_ttl_s:
            m = self._mem[key] = _Memory(self.memory_turns)
        m.last = self.clock()
        return m

    def forget(self, key):
        self._mem.pop(key, None)

    # --- tools --------------------------------------------------------------
    def _executor(self, speaker_discord_id):
        async def run(name, args):
            return await asyncio.to_thread(self.botapi.call_tool, name, args,
                                           speaker_discord_id)
        return run

    def _system(self, extra=""):
        return f"{PERSONA}\nHoje é {self.now():%d/%m/%Y %H:%M}.\n{extra}".strip()

    async def _llm(self, system, messages, speaker_discord_id=None):
        try:
            return await run_tools_loop(self.provider, system, messages, self._catalog,
                                        self._executor(speaker_discord_id))
        except LLMError as e:
            raise BrainUnavailable(str(e)) from e

    # --- conversa -----------------------------------------------------------
    async def answer(self, key, speaker_name, text, speaker_discord_id=None,
                     speaker_linked=True, voice=False, mentions=None):
        mem = self.memory(key)
        who = (f"Quem está falando: {speaker_name}."
               if speaker_linked else
               f"Quem está falando: {speaker_name} (NÃO vinculado ao grupo: \"eu\" não funciona; "
               f"se ele perguntar de si mesmo, diga para o admin rodar /x9 vincular).")
        extra = [who]
        if mem.focus:
            extra.append(f"PARTIDA EM FOCO: match_id={mem.focus['match_id']} — {mem.focus['desc']}.")
        if mentions:
            extra.append("Menções na mensagem: " + ", ".join(f"<@{i}> = {n}" for i, n in mentions.items())
                         + ". Passe a menção <@id> como jogador.")
        if voice:
            extra.append(VOICE_RULES)
        messages = list(mem.turns) + [{"role": "user", "content": f"{speaker_name}: {text}"}]
        reply, trail = await self._llm(self._system("\n".join(extra)), messages, speaker_discord_id)
        focus = focus_from_trail(trail)
        if focus:
            mem.focus = focus
        mem.turns.append({"role": "user", "content": f"{speaker_name}: {text}"})
        mem.turns.append({"role": "assistant", "content": reply})
        return reply

    # --- dedo-duro ----------------------------------------------------------
    async def gossip(self, match_id):
        prompt = (f"Acabou de cair uma partida nova do grupo (match_id={match_id}). Chame "
                  f"resumo_partida e momentos_chave com partida=\"{match_id}\" e faça o DEDO-DURO para "
                  f"o canal: quem carregou, quem afundou (com o número que prova), o round mais "
                  f"vergonhoso e o mais bonito. Máximo 8 linhas, com zoeira.")
        reply, _ = await self._llm(self._system(), [{"role": "user", "content": prompt}])
        return reply

    async def gossip_digest(self, match_ids):
        ids = ", ".join(match_ids)
        prompt = (f"Fiquei offline e caíram {len(match_ids)} partidas do grupo (match_ids: {ids}). "
                  f"Use partidas_recentes (limite={min(15, len(match_ids))}) e, se precisar, "
                  f"resumo_partida. Faça UM resumo dedo-duro de tudo que rolou enquanto você tava "
                  f"off: saldo de vitórias, quem carregou a sessão, quem foi o peso morto. Máximo 10 linhas.")
        reply, _ = await self._llm(self._system(), [{"role": "user", "content": prompt}])
        return reply

    async def voice_line(self, match_id):
        prompt = (f"Partida nova do grupo (match_id={match_id}). Chame resumo_partida com "
                  f"partida=\"{match_id}\" e anuncie na call em no máximo 45 palavras: resultado, "
                  f"placar, quem carregou e quem afundou.")
        reply, _ = await self._llm(self._system(VOICE_RULES), [{"role": "user", "content": prompt}])
        return reply


UNAVAILABLE_REPLY = ("Tô sem cérebro agora: o Gemini e o Groq não responderam (cota ou rede). "
                     "Tenta de novo daqui a pouco.")


def split_message(text, limit=1900):
    """Quebra em pedaços que cabem numa mensagem do Discord (2000 chars)."""
    text = text.strip()
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out or ["…"]


_MENTION = re.compile(r"<@!?(\d+)>")


def mention_ids(text):
    return _MENTION.findall(text or "")
