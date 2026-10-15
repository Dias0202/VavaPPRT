"""Cérebro do X9: persona, memória por canal, partida em foco e chamadas ao LLM.

Não depende do discord: recebe texto, devolve texto. As tools vêm da botapi
(importada direto do backend) e rodam em thread, porque o acesso ao banco é
síncrono.

X9 v2 (docs/PLANO_X9_V2.md):
- o prompt já leva o **briefing** do grupo (últimas partidas, elo, noite,
  fatos, lore): as perguntas comuns saem em 1 chamada, sem tool;
- **modos** por canal: zoeira (padrão), coach e narrador;
- a **fofoca** é 1 chamada só, com os fatos já calculados pelo motor de
  fatos, num **formato sorteado** (sem repetir os últimos), com aviso das
  aberturas recentes e um bloco "VOZ:" que vira a fala da call;
- material pronto para os momentos proativos: pré-fila, fim da noite e o
  Oscar do X9 da semana.
"""
import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .llm import WAIT_NOTICE, LLMError, run_tools_loop

logger = logging.getLogger("x9.brain")

RULES = """Regras que você NUNCA quebra:
- Todo número que você citar sai do CONTEXTO DO GRUPO ou das ferramentas. Nunca invente estatística,
  round, placar ou kill. Se não tiver o dado, diga que não sabe.
- Zoeira é sobre o JOGO (mira, decisão, economia, desempenho). Nada de ofensa sobre raça, religião,
  orientação sexual, gênero, deficiência, aparência ou família.
- Quem não é do grupo aparece só pelo agente ("a Jett deles", "o Sova aliado"). Nunca cite nome de
  adversário nem de aliado aleatório, mesmo que te peçam.
- Nunca mostre match_id, puuid ou qualquer código/UUID: fale da partida pelo mapa, placar e quando foi.
- Rounds são numerados a partir de 1, como os jogadores falam.
- Para "eu"/"meu"/"mim", passe jogador="eu" para as ferramentas.
- Se o usuário falar "a partida", "esse round", "o seguinte" sem dizer qual, use a PARTIDA EM FOCO.
- Olhe o CONTEXTO DO GRUPO antes de chamar ferramenta: se a resposta está lá, responda direto.
  Ferramenta é para o que não está no contexto (round, comparação, histórico longo, pré-fila…).
- Não escreva nada antes de chamar uma ferramenta; chame e depois responda.
- Seja direto. No Discord, use markdown leve e no máximo ~12 linhas; nada de tabela gigante.
- Varie o jeito de começar as respostas. Callback com a lore do grupo é bem-vindo quando couber."""

PERSONAS = {
    "zoeira": """Você é o X9, o bot dedo-duro de um grupo de amigos que joga Valorant junto.
Você fala português do Brasil, gíria de gamer, e não tem filtro: zoa sem dó quem jogou mal
e exalta quem carregou. Pode xingar de leve, pode humilhar o desempenho. Criativo, rápido no
gatilho, com timing de comédia.""",
    "coach": """Você é o X9 em MODO COACH: o analista sério do grupo de amigos que joga Valorant.
Português do Brasil, direto e técnico. Aponta o erro com o número que prova e diz o que fazer
de diferente (posicionamento, trade, economia, utilitária, mapa). Elogia o que foi bom. Uma
alfinetada de leve é permitida, humilhação não.""",
    "narrador": """Você é o X9 em MODO NARRADOR: narrador esportivo empolgado que transmite as partidas
de um grupo de amigos no Valorant. Português do Brasil, bordões, emoção, exagero teatral — mas
cada lance narrado é verdadeiro e vem com o número.""",
}
MODES = tuple(PERSONAS)
PERSONA = PERSONAS["zoeira"] + "\n\n" + RULES  # compat: a persona padrão completa

VOICE_RULES = """MODO VOZ: sua resposta vai ser FALADA na call. Responda em no máximo 3 frases curtas,
sem markdown, sem emoji, sem listas, sem links. Escreva números como se fala ("13 a 8")."""

# Formatos da fofoca: sorteados, sem repetir os últimos.
FORMATS = {
    "plantao": "PLANTÃO URGENTE de telejornal: manchete em caixa alta e a notícia em tom de urgência.",
    "futebol": "narração de FUTEBOL estilo Galvão: lance a lance, com bordão e replay.",
    "ocorrencia": "BOLETIM DE OCORRÊNCIA policial: ocorrência, vítimas, suspeito, testemunhas, provas.",
    "laudo": "LAUDO MÉDICO: paciente, sintomas, exames (os números), diagnóstico e prescrição.",
    "tempo": "PREVISÃO DO TEMPO: frente fria de derrotas, chuva de headshot, alerta de tilt.",
    "revista": "REVISTA DE FOFOCA de celebridade: 'EXCLUSIVO', 'fontes próximas afirmam'.",
    "review": "REVIEW com estrelas (de 1 a 5 ⭐) para cada um do grupo, com uma linha de crítica.",
    "linkedin": "post motivacional de LINKEDIN, irônico, com 'aprendizados' e hashtags no fim.",
    "trailer": "TRAILER DE FILME: 'Num mundo onde…', elenco, classificação indicativa.",
    "ufc": "narração de LUTA DE UFC: anúncio do card, trocação, nocaute e quem bateu o tapinha.",
    "rh": "RELATÓRIO DE RH: avaliação de desempenho, advertência formal, promoção.",
    "terapia": "SESSÃO DE TERAPIA do grupo: o terapeuta conduz e cada um 'desabafa' os números.",
    "documentario": "DOCUMENTÁRIO DE VIDA SELVAGEM, com o narrador observando a espécie em seu habitat.",
    "receita": "RECEITA: ingredientes (os erros e acertos) e modo de preparo do resultado.",
}
NO_REPEAT_LAST = 3


class BrainUnavailable(Exception):
    """Nenhum LLM respondeu (cota estourada nos dois, rede fora…)."""


@dataclass
class Gossip:
    text: str
    voice: str
    fmt: str = None
    hooks: list = field(default_factory=list)


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


_VOICE_SPLIT = re.compile(r"^\s*\**\s*VOZ\s*\**\s*:\s*", re.I | re.M)


def split_voice(reply):
    """(texto do canal, fala da call). Sem o bloco VOZ, a fala sai do começo do texto."""
    parts = _VOICE_SPLIT.split(reply or "", maxsplit=1)
    text = parts[0].strip()
    voice = parts[1].strip() if len(parts) > 1 else ""
    if not voice:
        plain = re.sub(r"[*_`#>~|]", "", text)
        voice = " ".join(re.split(r"(?<=[.!?])\s+", plain.replace("\n", " "))[:2]).strip()
    return text, voice[:400]


def _opening(text):
    line = next((x for x in (text or "").splitlines() if x.strip()), "")
    return re.sub(r"\s+", " ", line).strip()[:90]


class Brain:
    def __init__(self, provider, botapi, *, memory_turns=10, memory_ttl_s=900,
                 clock=time.monotonic, now=datetime.now, local_tools=None, routes=None,
                 briefing=True, context_providers=None, telemetry=None, rng=None, store=None):
        self.provider, self.botapi = provider, botapi
        # Rotas: provedores por tarefa (voz prefere o mais rápido; fofoca, o
        # mais criativo). Sem rotas, tudo usa `provider`.
        self.routes = {"texto": provider, "voz": provider, "fofoca": provider, **(routes or {})}
        self.memory_turns, self.memory_ttl_s = memory_turns, memory_ttl_s
        self.clock, self.now = clock, now
        self.telemetry = telemetry
        self.rng = rng or random.Random()
        self.store = store if store is not None else getattr(botapi, "db", None)
        self._mem = {}
        self._modes = {}
        self.briefing_fn = getattr(botapi, "briefing", None) if briefing else None
        self.context_providers = list(context_providers or [])
        # Tools que só o bot sabe responder (ex.: quem está jogando agora, pela
        # presença do Discord): {nome: (fn(args) -> dict, descrição, schema)}.
        self.local_tools = dict(local_tools or {})
        catalog = botapi.llm_catalog() if hasattr(botapi, "llm_catalog") else botapi.tool_catalog()
        self._catalog = catalog + [
            {"name": n, "description": d, "parameters": p}
            for n, (_, d, p) in self.local_tools.items()]

    # --- memória ------------------------------------------------------------
    def memory(self, key):
        m = self._mem.get(key)
        if m is None or self.clock() - m.last > self.memory_ttl_s:
            m = self._mem[key] = _Memory(self.memory_turns)
        m.last = self.clock()
        return m

    def forget(self, key):
        self._mem.pop(key, None)

    # --- modo por canal (zoeira | coach | narrador) -------------------------
    def load_modes(self):
        """Lê todos os modos salvos numa query (bloqueante: rode em thread no boot)."""
        if self.store is None or not hasattr(self.store, "config_items"):
            return
        for k, v in self.store.config_items("x9_mode:").items():
            if v in PERSONAS:
                self._modes[k.split(":", 1)[1]] = v
        self._modes_loaded = True

    def mode_for(self, channel_id):
        if channel_id is None:
            return "zoeira"
        key = str(channel_id)
        if key not in self._modes and getattr(self, "_modes_loaded", False):
            return "zoeira"  # carregado no boot: não precisa ir ao banco
        if key not in self._modes:
            v = None
            if self.store is not None:
                try:
                    v = self.store.get_config(f"x9_mode:{key}")
                except Exception:
                    logger.exception("Não consegui ler o modo do canal")
            self._modes[key] = v if v in PERSONAS else "zoeira"
        return self._modes[key]

    def set_mode(self, channel_id, mode):
        if mode not in PERSONAS:
            raise ValueError(f"Modo inválido: {mode}")
        self._modes[str(channel_id)] = mode
        if self.store is not None:
            self.store.set_config(f"x9_mode:{channel_id}", mode)

    # --- tools --------------------------------------------------------------
    def _executor(self, speaker_discord_id):
        async def run(name, args):
            if name in self.local_tools:
                try:
                    return self.local_tools[name][0](args or {})
                except Exception:
                    logger.exception("Tool local %s falhou", name)
                    return {"erro": "Deu ruim aqui dentro ao buscar isso."}
            return await asyncio.to_thread(self.botapi.call_tool, name, args,
                                           speaker_discord_id)
        return run

    # --- contexto -----------------------------------------------------------
    async def context(self):
        parts = []
        if self.briefing_fn:
            try:
                parts.append(await asyncio.to_thread(self.briefing_fn))
            except Exception:
                logger.exception("Briefing falhou (seguindo sem)")
        for fn in self.context_providers:
            try:
                s = fn()
                if s:
                    parts.append(s)
            except Exception:
                logger.exception("Contexto extra falhou")
        return "\n".join(p for p in parts if p)

    async def prewarm(self):
        """Partida nova: deixa o briefing pronto antes da próxima pergunta."""
        if self.briefing_fn:
            try:
                await asyncio.to_thread(self.briefing_fn)
            except Exception:
                logger.exception("Pré-aquecimento do briefing falhou")

    def _system(self, extra="", mode="zoeira", context=""):
        persona = PERSONAS.get(mode, PERSONAS["zoeira"])
        ctx = f"\nCONTEXTO DO GRUPO (atualizado agora; use antes de chamar ferramenta):\n{context}\n" \
            if context else ""
        return f"{persona}\n\n{RULES}\nHoje é {self.now():%d/%m/%Y %H:%M}.{ctx}\n{extra}".strip()

    async def _llm(self, system, messages, speaker_discord_id=None, route="texto",
                   on_text=None, temperature=0.75, stats=None):
        try:
            return await run_tools_loop(self.routes[route], system, messages, self._catalog,
                                        self._executor(speaker_discord_id), on_text=on_text,
                                        temperature=temperature, stats=stats)
        except LLMError as e:
            raise BrainUnavailable(str(e)) from e

    async def _once(self, system, prompt, route="fofoca", temperature=1.0):
        """Uma chamada só, sem tools (fofoca e posts proativos)."""
        t0 = time.perf_counter()
        try:
            resp = await self.routes[route].chat(system, [{"role": "user", "content": prompt}],
                                                 None, temperature=temperature)
        except LLMError as e:
            raise BrainUnavailable(str(e)) from e
        if self.telemetry:
            self.telemetry.record_answer(route, (time.perf_counter() - t0) * 1000,
                                         {"steps": 1, "provider": resp.provider, "tools": []})
        if not resp.text:
            raise BrainUnavailable(f"{resp.provider or 'LLM'} respondeu vazio")
        return resp.text

    # --- conversa -----------------------------------------------------------
    async def answer(self, key, speaker_name, text, speaker_discord_id=None,
                     speaker_linked=True, voice=False, mentions=None, mode=None,
                     on_text=None, on_wait=None):
        t0 = time.perf_counter()
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
        context = await self.context()
        system = self._system("\n".join(extra), mode or "zoeira", context)
        messages = list(mem.turns) + [{"role": "user", "content": f"{speaker_name}: {text}"}]
        stats = {}
        token = WAIT_NOTICE.set(on_wait) if on_wait else None
        try:
            reply, trail = await self._llm(system, messages, speaker_discord_id,
                                           route="voz" if voice else "texto", on_text=on_text,
                                           temperature=0.75, stats=stats)
        finally:
            if token is not None:
                WAIT_NOTICE.reset(token)
        if self.telemetry:
            self.telemetry.record_answer("voz" if voice else "texto",
                                         (time.perf_counter() - t0) * 1000, stats)
        focus = focus_from_trail(trail)
        if focus:
            mem.focus = focus
        mem.turns.append({"role": "user", "content": f"{speaker_name}: {text}"})
        mem.turns.append({"role": "assistant", "content": reply})
        return reply

    # --- dedo-duro ----------------------------------------------------------
    def _recent(self):
        if self.store is None or not hasattr(self.store, "recent_gossips"):
            return []
        try:
            return self.store.recent_gossips(15)
        except Exception:
            logger.exception("Não consegui ler as fofocas recentes")
            return []

    def pick_format(self, recent=None):
        recent = self._recent() if recent is None else recent
        last = [r.get("gossip_format") for r in recent[:NO_REPEAT_LAST]]
        options = [k for k in FORMATS if k not in last] or list(FORMATS)
        return self.rng.choice(options)

    def _avoid(self, recent):
        openings = [o for o in (_opening(r.get("gossip_text")) for r in recent[:8]) if o]
        if not openings:
            return ""
        return ("Não repita estas aberturas nem piadas recentes:\n- " + "\n- ".join(openings) + "\n")

    async def gossip(self, match_id, mode="zoeira"):
        """Fofoca de uma partida em UMA chamada: texto do canal + fala da call."""
        mat = await asyncio.to_thread(self.botapi.material_fofoca, match_id)
        hooks = mat.pop("_hooks", [])
        mat.pop("amigos_puuids", None)
        recent = self._recent()
        fmt = self.pick_format(recent)
        prompt = (
            f"Acabou de cair uma partida nova do grupo. Escreva o DEDO-DURO para o canal no formato: "
            f"{FORMATS[fmt]}\n"
            "Regras: no máximo 8 linhas (markdown leve do Discord). Use pelo menos 2 dos FATOS "
            "(são verdade e são o que mais surpreende), sempre com o número que prova. Exalte quem "
            "carregou, zoe quem afundou, feche com uma frase de efeito.\n"
            f"{self._avoid(recent)}"
            "Depois, numa linha separada, escreva \"VOZ:\" e uma versão FALADA de no máximo 40 "
            "palavras (sem markdown nem emoji) com resultado, placar, quem carregou e quem afundou.\n\n"
            f"DADOS:\n{json.dumps(mat, ensure_ascii=False, default=str)}")
        reply = await self._once(self._system(mode=mode), prompt, "fofoca", temperature=1.05)
        text, voice = split_voice(reply)
        return Gossip(text=text, voice=voice, fmt=fmt, hooks=hooks)

    async def gossip_digest(self, match_ids, mode="zoeira"):
        """Muitas partidas pendentes (bot ficou off): um resumo só."""
        ids = list(match_ids)[-8:]
        mats = []
        for mid in ids:
            try:
                m = await asyncio.to_thread(self.botapi.material_fofoca, mid)
            except Exception:
                logger.exception("Material da partida %s falhou", mid[:8])
                continue
            m.pop("_hooks", None)
            m.pop("amigos_puuids", None)
            mats.append({k: m[k] for k in ("partida", "amigos", "mvp_do_grupo", "pior_do_grupo",
                                           "fatos")})
        prompt = (
            f"Fiquei offline e caíram {len(match_ids)} partidas do grupo. Faça UM resumo dedo-duro "
            "de tudo que rolou enquanto você tava off: saldo de vitórias, quem carregou a sessão, quem "
            "foi o peso morto, o fato mais vergonhoso. Máximo 10 linhas, com os números.\n"
            "Depois, numa linha separada, \"VOZ:\" e uma versão falada de até 40 palavras.\n\n"
            f"PARTIDAS (mais antigas primeiro):\n{json.dumps(mats, ensure_ascii=False, default=str)}")
        reply = await self._once(self._system(mode=mode), prompt, "fofoca", temperature=1.0)
        text, voice = split_voice(reply)
        return Gossip(text=text, voice=voice, fmt="digest")

    # --- momentos proativos -------------------------------------------------
    async def night_recap(self, mode="zoeira", since=None, min_matches=2):
        """since: quando a sessão de presença começou. Sem partida depois disso
        (abriu o jogo e não jogou), não há noite para resenhar — e a anterior
        já teve a dela."""
        mat = await asyncio.to_thread(self.botapi.material_noite)
        if not mat.get("tem_noite") or mat.get("partidas", 0) < min_matches:
            return None
        if since is not None and (mat.get("fim_raw") or 0) < since - 3600:
            return None
        prompt = ("A noite de jogo do grupo acabou (todo mundo fechou o jogo). Faça a RESENHA DA NOITE: "
                  "saldo, MVP da noite, bagre da noite, quem tiltou (K/D caindo), RR de cada um e o "
                  "melhor/pior momento. Máximo 10 linhas, com os números.\n\n"
                  f"DADOS:\n{json.dumps(mat, ensure_ascii=False, default=str)}")
        return await self._once(self._system(mode=mode), prompt, "fofoca", temperature=0.95)

    async def prefila(self, puuids, mode="zoeira"):
        mat = await asyncio.to_thread(self.botapi.material_prefila, list(puuids))
        if not mat:
            return None
        prompt = ("Esses jogadores do grupo acabaram de abrir o VALORANT juntos. Faça a PRELEÇÃO de "
                  "técnico antes da fila: quais mapas aceitar e quais evitar (com o número que prova "
                  "e a base da evidência) e a escalação sugerida. Se não houver amostra, diga. Máximo "
                  "7 linhas.\n\n"
                  f"DADOS:\n{json.dumps(mat, ensure_ascii=False, default=str)}")
        return await self._once(self._system(mode=mode), prompt, "fofoca", temperature=0.85)

    async def oscar(self, mode="zoeira"):
        mat = await asyncio.to_thread(self.botapi.material_oscar)
        if not (mat.get("semana") or {}).get("partidas"):
            return None
        prompt = ("É domingo à noite: entregue o OSCAR DO X9 da semana. Categorias (pule a que não "
                  "tiver dado): MVP da semana, Bagre da semana, os apelidos da semana, Melhor dupla, "
                  "Melhor partida, Pior partida, Quem mais subiu de elo. Para cada uma: o vencedor e a "
                  "prova (número). Clima de cerimônia, com discurso de agradecimento curto do MVP. "
                  "Máximo 16 linhas.\n\n"
                  f"DADOS:\n{json.dumps(mat, ensure_ascii=False, default=str)}")
        return await self._once(self._system(mode=mode), prompt, "fofoca", temperature=1.0)


UNAVAILABLE_REPLY = ("Tô sem cérebro agora: o Gemini e o Groq não responderam (cota ou rede). "
                     "Tenta de novo daqui a pouco.")


class SentenceStream:
    """Junta o texto em streaming e solta frases completas (para a voz falar
    a 1ª frase enquanto o LLM ainda escreve a 2ª)."""

    # fim de frase: pontuação seguida de espaço (não corta "13.5"), ou quebra de linha
    _BOUNDARY = re.compile(r"[.!?…]+[\"')\]]*\s+|\n+")

    def __init__(self, min_chars=12):
        self.buf, self.step, self.min_chars = "", None, min_chars

    def feed(self, delta, step=0):
        if step != self.step:
            # Passo novo do loop de tools: o que veio antes era só "Vou ver.".
            self.buf, self.step = "", step
        self.buf += delta
        out, pos = [], 0
        while True:
            m = self._BOUNDARY.search(self.buf, pos)
            if not m:
                break
            sentence = self.buf[:m.end()].strip()
            if len(sentence) < self.min_chars:
                pos = m.end()  # curtinha ("Eita.") vai junto com a próxima
                continue
            out.append(sentence)
            self.buf, pos = self.buf[m.end():], 0
        return out

    def flush(self):
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


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
