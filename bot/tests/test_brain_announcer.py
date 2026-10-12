"""Cérebro (memória, foco, persona) e loop do dedo-duro, contra o banco de teste."""
import time

import pytest

from conftest import ADMIN_DISCORD
from x9.announcer import Announcer
from x9.brain import Brain, BrainUnavailable, focus_from_trail, split_message
from x9.llm import LLMError, LLMResponse


class Scripted:
    """LLM falso: cada resposta pode ser LLMResponse, Exception ou função(system, msgs)."""

    name = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def chat(self, system, messages, tools=None, temperature=0.9):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r(system, messages) if callable(r) else r


def call(name, **args):
    return LLMResponse(tool_calls=[{"id": "c", "name": name, "args": args}], provider="fake")


def text(t):
    return LLMResponse(text=t, provider="fake")


# ---------------------------------------------------------------------------
# Brain
# ---------------------------------------------------------------------------
async def test_resposta_usa_tool_real_e_fixa_partida_em_foco(backend):
    llm = Scripted([call("narrar_round", round=1), text("Round 1: admin abriu.")])
    brain = Brain(llm, backend.botapi)
    out = await brain.answer("text:1", "admin", "conta o round 1", speaker_discord_id=ADMIN_DISCORD)
    assert out == "Round 1: admin abriu."
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"]["round"] == 1
    focus = brain.memory("text:1").focus
    assert focus and focus["match_id"] == tool_msg["content"]["partida"]["match_id"]

    # 2ª pergunta: o system prompt carrega a partida em foco e o histórico
    llm.replies = [text("Round 2 também.")]
    await brain.answer("text:1", "admin", "e o seguinte?", speaker_discord_id=ADMIN_DISCORD)
    sys2, msgs2 = llm.calls[-1]["system"], llm.calls[-1]["messages"]
    assert f"PARTIDA EM FOCO: match_id={focus['match_id']}" in sys2
    assert [m["role"] for m in msgs2] == ["user", "assistant", "user"]
    assert msgs2[0]["content"] == "admin: conta o round 1"


async def test_eu_resolve_pelo_vinculo(backend):
    llm = Scripted([call("desempenho_jogador", jogador="eu"), text("ok")])
    brain = Brain(llm, backend.botapi)
    await brain.answer("k", "admin", "como eu tô?", speaker_discord_id=ADMIN_DISCORD)
    assert llm.calls[1]["messages"][-1]["content"]["jogador"] == "admin"

    llm.replies = [call("desempenho_jogador", jogador="eu"), text("ok")]
    await brain.answer("k2", "Fulano", "e eu?", speaker_discord_id="999", speaker_linked=False)
    assert "vincular" in llm.calls[-1]["messages"][-1]["content"]["erro"]
    assert "NÃO vinculado" in llm.calls[-2]["system"]


async def test_memoria_expira_e_e_por_canal(backend):
    t = [0.0]
    llm = Scripted([text("a"), text("b"), text("c")])
    brain = Brain(llm, backend.botapi, memory_ttl_s=900, clock=lambda: t[0])
    await brain.answer("canal-a", "admin", "um")
    await brain.answer("canal-b", "admin", "dois")
    assert len(llm.calls[-1]["messages"]) == 1  # canal-b não vê o canal-a
    t[0] = 1000
    await brain.answer("canal-a", "admin", "três")
    assert len(llm.calls[-1]["messages"]) == 1  # expirou


async def test_modo_voz_e_mencoes_entram_no_prompt(backend):
    llm = Scripted([text("curto")])
    brain = Brain(llm, backend.botapi)
    await brain.answer("v", "admin", "e o Luan?", voice=True, mentions={42: "Luan"})
    s = llm.calls[0]["system"]
    assert "MODO VOZ" in s and "<@42> = Luan" in s
    assert "Nunca cite nome de\n  adversário" in s or "Nunca cite nome de adversário" in s.replace("\n  ", " ")


async def test_sem_llm_vira_brain_unavailable(backend):
    brain = Brain(Scripted([LLMError("cota")]), backend.botapi)
    with pytest.raises(BrainUnavailable):
        await brain.answer("k", "admin", "oi")


def test_foco_a_partir_da_trilha():
    trail = [("ranking_grupo", {}, {"ranking": []}),
             ("partidas_recentes", {}, {"partidas": [{"match_id": "m1", "mapa": "Bind", "placar": "13x2"}]}),
             ("narrar_round", {}, {"erro": "x"})]
    assert focus_from_trail(trail) == {"match_id": "m1", "desc": "Bind 13x2"}
    assert focus_from_trail([("x", {}, {"erro": "y"})]) is None


def test_quebra_mensagem_longa():
    parts = split_message(("linha de texto\n" * 300).strip(), limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts).replace("\n", "") == ("linha de texto" * 300)
    assert split_message("") == ["…"]


# ---------------------------------------------------------------------------
# Announcer
# ---------------------------------------------------------------------------
class FakePoster:
    def __init__(self, voice=False):
        self.embeds, self.threads, self.spoken, self.voice = [], [], [], voice

    async def post_embed(self, channel_id, embed):
        self.embeds.append((channel_id, embed))
        return 5000 + len(self.embeds)

    async def post_in_thread(self, channel_id, message_id, title, text):
        self.threads.append((channel_id, message_id, title, text))
        return int(message_id)

    def in_voice(self):
        return self.voice

    async def speak(self, text):
        self.spoken.append(text)
        return True


class FakeBrain:
    def __init__(self, fail=False):
        self.fail, self.gossiped, self.digests = fail, [], []

    async def gossip(self, mid):
        if self.fail:
            raise BrainUnavailable("x")
        self.gossiped.append(mid)
        return f"fofoca {mid[-1]}"

    async def gossip_digest(self, mids):
        self.digests.append(mids)
        return "resumo"

    async def voice_line(self, mid):
        return f"voz {mid[-1]}"


def _announcer(backend, brain, poster, **kw):
    kw.setdefault("channel_id", 777)
    return Announcer(brain, poster, backend, clock=time.time, **kw)


async def test_bot_posta_placar_quando_o_poller_nao_postou(backend):
    backend.db.set_config(backend.announce.SINCE_KEY, 0)
    poster = FakePoster()
    ann = _announcer(backend, FakeBrain(), poster)
    assert await ann.fallback_scoreboards() == 4
    assert all(ch == 777 for ch, _ in poster.embeds)
    assert await ann.fallback_scoreboards() == 0
    a = backend.db.announcements_pending_gossip(0)
    assert len(a) == 4 and {x["claimed_by"] for x in a} == {"bot"}


async def test_bot_espera_a_vez_do_poller(backend):
    backend.db.set_config(backend.announce.SINCE_KEY, 0)
    poster = FakePoster()
    mids = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 10)
    newest = backend.db.get_match_row(mids[0])["started_raw"]
    # 10 min depois do início da mais nova: ela ainda é do poller, as outras não
    ann = Announcer(FakeBrain(), poster, backend, channel_id=777, clock=lambda: newest + 600)
    assert await ann.fallback_scoreboards() == 3
    assert backend.db.get_announcement(mids[0]) is None
    assert await _announcer(backend, FakeBrain(), poster, post_scoreboard=False).fallback_scoreboards() == 0
    assert await _announcer(backend, FakeBrain(), poster, channel_id=None).fallback_scoreboards() == 0


async def test_fofoca_na_thread_e_voz(backend):
    db = backend.db
    mids = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 10)[:2]
    for i, mid in enumerate(mids):
        assert db.claim_announcement(mid, "poller")
        db.update_announcement(mid, channel_id="777", message_id=str(900 + i))
    poster, brain = FakePoster(voice=True), FakeBrain()
    ann = _announcer(backend, brain, poster)
    assert await ann.gossip_pending() == 2
    assert [t[1] for t in poster.threads] == ["901", "900"] or [t[1] for t in poster.threads] == ["900", "901"]
    assert len(poster.spoken) == 2
    for mid in mids:
        a = db.get_announcement(mid)
        assert a["gossip_raw"] and a["voice_raw"] and a["thread_id"] == a["message_id"]
    assert await ann.gossip_pending() == 0  # não repete


async def test_atraso_grande_vira_resumo_unico(backend):
    db = backend.db
    db.set_config(backend.announce.SINCE_KEY, 0)
    poster, brain = FakePoster(), FakeBrain()
    ann = _announcer(backend, brain, poster)
    await ann.fallback_scoreboards()          # 4 placares, nenhuma fofoca ainda
    assert await ann.gossip_pending() == 4
    assert len(brain.digests) == 1 and len(brain.digests[0]) == 4
    assert len(poster.threads) == 1 and poster.threads[0][2] == "X9 · enquanto eu tava off"
    assert not brain.gossiped


async def test_sem_llm_a_fofoca_fica_pendente(backend):
    db = backend.db
    mid = db.group_matches(sorted(backend.botapi._Ctx().friends), 1)[0]
    db.claim_announcement(mid, "poller")
    db.update_announcement(mid, channel_id="1", message_id="2")
    ann = _announcer(backend, FakeBrain(fail=True), FakePoster())
    assert await ann.gossip_pending() == 0
    assert db.get_announcement(mid)["gossip_raw"] is None
    r = await _announcer(backend, FakeBrain(), FakePoster()).tick()
    assert r["fofocas"] == 1


async def test_anuncio_antigo_demais_nao_ganha_fofoca(backend):
    db = backend.db
    mid = db.group_matches(sorted(backend.botapi._Ctx().friends), 1)[0]
    db.claim_announcement(mid, "poller")
    db.update_announcement(mid, channel_id="1", message_id="2")
    ann = Announcer(FakeBrain(), FakePoster(), backend, clock=lambda: time.time() + 49 * 3600)
    assert await ann.gossip_pending() == 0
