"""X9 v2 (docs/PLANO_X9_V2.md): fofoca sem duplicata nem retrabalho, ao-vivo por
partida/party, cérebro com briefing/modos/formatos, momentos proativos,
mensagem ao vivo e telemetria."""
import asyncio
import random
import time
from datetime import datetime

import pytest

from conftest import ADMIN_DISCORD, ADMIN_PUUID, FRIEND_PUUID, load_matches
from test_brain_announcer import FakeBrain, FakePoster, Scripted, text
from x9.announcer import Announcer
from x9.brain import FORMATS, Brain, BrainUnavailable, split_voice
from x9.live import LiveTracker
from x9.llm import LLMError, LLMResponse
from x9.moments import OSCAR_KEY, Moments
from x9.streaming import CURSOR, LiveMessage
from x9.telemetry import Telemetry, match_summary, percentile


def _pending(backend, n=1):
    db = backend.db
    mids = db.group_matches(sorted(backend.botapi._Ctx().friends), 10)[:n]
    for i, mid in enumerate(mids):
        db.claim_announcement(mid, "poller")
        db.update_announcement(mid, channel_id="777", message_id=str(900 + i),
                               posted_raw=int(time.time()))
    return mids


class FullPoster(FakePoster):
    """Poster com os opcionais (reação, MVP, edição, post solto)."""

    def __init__(self, fail_thread=0, **kw):
        super().__init__(**kw)
        self.fail_thread, self.reactions, self.votes, self.edits, self.loose = fail_thread, [], [], [], []

    async def post_in_thread(self, channel_id, message_id, title, text):
        if self.fail_thread:
            self.fail_thread -= 1
            raise RuntimeError("thread arquivada")
        return await super().post_in_thread(channel_id, message_id, title, text)

    async def react(self, channel_id, message_id, emojis):
        self.reactions.append((message_id, emojis))

    async def post_mvp_vote(self, channel_id, message_id, match_id, labels):
        self.votes.append((match_id, labels))

    async def edit_embed(self, channel_id, message_id, embed):
        self.edits.append((message_id, embed))
        return True

    async def post_text(self, channel_id, text):
        self.loose.append(text)
        return 1


# ---------------------------------------------------------------------------
# Fofoca: sem duplicata, sem pagar o LLM de novo, desistência, kick
# ---------------------------------------------------------------------------
async def test_tick_e_ao_vivo_ao_mesmo_tempo_nao_duplicam_a_fofoca(backend):
    mids = _pending(backend, 2)
    brain, poster = FakeBrain(delay=0.05), FakePoster()
    a1 = Announcer(brain, poster, backend, channel_id=777)
    a2 = Announcer(brain, poster, backend, channel_id=777)  # "2ª instância"
    r = await asyncio.gather(a1.gossip_pending(), a1.gossip_pending(), a2.gossip_pending())
    assert sum(r) == 2
    assert sorted(t[1] for t in poster.threads) == ["900", "901"]
    assert sorted(brain.gossiped) == sorted(mids)


async def test_post_falhou_guarda_o_texto_e_nao_paga_o_llm_de_novo(backend):
    (mid,) = _pending(backend)
    brain, poster = FakeBrain(), FullPoster(fail_thread=1)
    ann = Announcer(brain, poster, backend, channel_id=777)
    assert await ann.gossip_pending() == 0
    a = backend.db.get_announcement(mid)
    assert a["gossip_raw"] is None and a["gossip_tries"] == 1 and a["gossip_text"]
    assert await ann.gossip_pending() == 1
    assert brain.gossiped == [mid]                       # gerou uma vez só
    assert poster.threads[0][3] == f"fofoca {mid[-1]}"


async def test_desiste_depois_de_n_tentativas_e_posta_solto(backend):
    (mid,) = _pending(backend)
    poster = FullPoster(fail_thread=99)
    ann = Announcer(FakeBrain(), poster, backend, channel_id=777, max_tries=2)
    await ann.gossip_pending()
    await ann.gossip_pending()
    assert poster.loose == [f"fofoca {mid[-1]}"]
    assert backend.db.get_announcement(mid)["gossip_raw"] > 0
    assert await ann.gossip_pending() == 0               # não tenta para sempre


async def test_kick_do_webhook_solta_a_fofoca_sem_esperar_o_tick(backend):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    poster = FakePoster()
    ann = Announcer(FakeBrain(), poster, backend, channel_id=777, sleep=fake_sleep)
    task = ann.kick()
    assert ann.kick() is task                            # um kick por vez
    await task                                           # nada pendente: tenta 3x e desiste
    assert slept == [1.5, 4, 9] and poster.threads == []
    _pending(backend)
    slept.clear()
    await ann.kick()
    assert slept == [1.5] and len(poster.threads) == 1


async def test_ao_vivo_posta_placar_e_fofoca_com_extras(backend):
    mid = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 10)[1]  # admin + amigo
    hooks = [{"tipo": "ace", "texto": "ACE do admin no round 3", "puuid": ADMIN_PUUID, "nota": 3},
             {"tipo": "ultimo_lobby", "texto": "amigo foi o ÚLTIMO do lobby", "nota": 2}]
    brain, poster = FakeBrain(hooks=hooks), FullPoster(voice=True)
    backend.db.place_bet("555", "vitoria", now=1)
    ann = Announcer(brain, poster, backend, channel_id=777)
    assert await ann.announce_now([mid]) == 1
    a = backend.db.get_announcement(mid)
    assert a["ended_raw"] and a["posted_raw"] >= a["ended_raw"] and a["gossip_raw"] > 0
    texts = [t[3] for t in poster.threads]
    assert texts[0] == f"fofoca {mid[-1]}"
    assert any(t.startswith("🎲 **Apostas**") and "<@555>" in t for t in texts)
    assert poster.spoken == [f"voz {mid[-1]}"]
    assert poster.reactions and "🎯" in poster.reactions[0][1] and "💀" in poster.reactions[0][1]
    assert poster.votes == [(mid, ["admin", "amigo"])]
    assert any("ACE do admin" in x for x in backend.botapi.lore_lines())
    assert backend.db.open_bets() == []


async def test_rr_reescreve_o_placar_e_anuncia_quem_mudou_de_elo(backend):
    mid = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 1)[0]
    poster = FullPoster(voice=True)
    ann = Announcer(FakeBrain(), poster, backend, channel_id=777)
    await ann.announce_now([mid])
    started = backend.db.get_match_row(mid)["started_raw"]
    backend.db.save_rank_history([
        {"puuid": ADMIN_PUUID, "match_id": "antes", "date_raw": started - 99, "tier": 12,
         "tier_name": "Gold 1", "rr": 90, "rr_change": 10},
        {"puuid": ADMIN_PUUID, "match_id": mid, "date_raw": started, "tier": 13,
         "tier_name": "Gold 2", "rr": 8, "rr_change": 18}])
    await ann.update_rr([mid])
    assert poster.edits and any("+18 RR" in f["value"] for f in poster.edits[0][1]["fields"])
    assert any("**admin** SUBIU pra **Gold 2** (+18 RR)" in t[3] for t in poster.threads)
    assert any("subiu pra Gold 2" in s for s in poster.spoken)


# ---------------------------------------------------------------------------
# Ao vivo: o stack sai junto da rajada; party checa como um só
# ---------------------------------------------------------------------------
def _drop(db, mid):
    conn = db.get_connection()
    try:
        ph = "?" if db._is_sqlite(conn) else "%s"
        conn.cursor().execute(f"DELETE FROM matches WHERE match_id = {ph}", (mid,))
        conn.commit()
    finally:
        conn.close()


async def test_partida_achada_tira_o_stack_inteiro_da_rajada(backend, henrik):
    both = [m for m in load_matches()
            if {ADMIN_PUUID, FRIEND_PUUID} <= {p["puuid"] for p in m["players"]}]
    mid = max(both, key=lambda m: m["metadata"]["started_at"])["metadata"]["match_id"]
    _drop(backend.db, mid)
    now = [10_000.0]
    seen = []

    async def on_new(ids):
        seen.append(ids)
    t = LiveTracker(backend, on_new, clock=lambda: now[0], spawn=lambda c: c, fetch_size=2)
    t.roster()
    for pu in (ADMIN_PUUID, FRIEND_PUUID):
        t.on_presence(pu, {"details": "x", "state": "y", "start": None, "party": None})
        t.on_presence(pu, None)  # os dois fecharam o jogo: rajada para os dois
    now[0] += 30
    henrik.calls.clear()
    assert await t.tick() == [mid] and seen == [[mid]]
    assert t.state[ADMIN_PUUID].burst == [] and t.state[FRIEND_PUUID].burst == []
    assert henrik.calls.count("v4") == 1                 # o amigo não consultou à toa
    assert await t.tick() == []


async def test_party_e_checada_como_um_so(backend, henrik):
    now = [10_000.0]

    async def on_new(ids):
        pass
    t = LiveTracker(backend, on_new, clock=lambda: now[0], spawn=lambda c: c)
    t.roster()
    act = {"details": "Competitive", "state": "In Match", "start": None, "party": "p1"}
    t.on_presence(ADMIN_PUUID, act)
    t.on_presence(FRIEND_PUUID, act)
    assert sorted(t.party_of(ADMIN_PUUID)) == sorted([ADMIN_PUUID, FRIEND_PUUID])
    for pu in (ADMIN_PUUID, FRIEND_PUUID):
        t.state[pu].next = now[0]
    henrik.calls.clear()
    await t.tick()
    assert henrik.calls.count("v4") == 1
    assert t.next_due() is None                          # o outro foi junto
    st = t.status()
    assert st["consultas_ultimo_min"] == 1 and {r["nome"] for r in st["jogadores"]} == {"admin", "amigo"}
    assert "admin" in t.playing_line() and "amigo" in t.playing_line()


# ---------------------------------------------------------------------------
# Cérebro
# ---------------------------------------------------------------------------
async def test_fofoca_e_uma_chamada_so_com_fatos_formato_e_voz(backend):
    mid = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 1)[0]
    reply = "🚨 PLANTÃO: admin carregou.\nOutra linha.\nVOZ: Vitória de 13 a 8, admin carregou."
    llm = Scripted([text(reply)])
    brain = Brain(llm, backend.botapi, rng=random.Random(1))
    g = await brain.gossip(mid)
    assert len(llm.calls) == 1 and llm.calls[0]["tools"] is None
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "FATOS" in prompt and FORMATS[g.fmt] in prompt and "VOZ:" in prompt
    assert "match_id" not in prompt.split("DADOS:")[1]
    assert g.text == "🚨 PLANTÃO: admin carregou.\nOutra linha."
    assert g.voice == "Vitória de 13 a 8, admin carregou."


def test_formato_nao_repete_os_ultimos(backend):
    brain = Brain(Scripted([]), backend.botapi, rng=random.Random(7))
    recent = [{"gossip_format": f} for f in ("plantao", "futebol", "laudo")]
    picks = {brain.pick_format(recent) for _ in range(200)}
    assert picks and not picks & {"plantao", "futebol", "laudo"}


async def test_aberturas_recentes_entram_no_pedido(backend):
    mids = _pending(backend, 1)
    backend.db.update_announcement(mids[0], gossip_raw=int(time.time()), gossip_format="ufc",
                                   gossip_text="🥊 NO CANTO AZUL, o admin!\nresto")
    llm = Scripted([text("x\nVOZ: y")])
    await Brain(llm, backend.botapi).gossip(mids[0])
    assert "NO CANTO AZUL, o admin!" in llm.calls[0]["messages"][0]["content"]


def test_split_voice_sem_bloco_usa_o_comeco():
    t, v = split_voice("**Linha** um. Linha dois! Três.")
    assert t == "**Linha** um. Linha dois! Três." and v == "Linha um. Linha dois!"


async def test_briefing_vai_no_prompt_e_modos(backend):
    llm = Scripted([text("a"), text("b")])
    brain = Brain(llm, backend.botapi)
    await brain.answer("k", "admin", "quem carregou?")
    s = llm.calls[0]["system"]
    assert "CONTEXTO DO GRUPO" in s and 'partida="1"' in s
    assert len(llm.calls[0]["tools"]) <= 9                # catálogo enxuto
    brain.set_mode(42, "coach")
    assert Brain(llm, backend.botapi).mode_for(42) == "coach"  # persiste no banco
    fresh = Brain(llm, backend.botapi)
    fresh.load_modes()                                    # boot: uma query para todos
    assert fresh._modes == {"42": "coach"} and fresh.mode_for(99) == "zoeira"
    await brain.answer("k2", "admin", "e aí?", mode="coach")
    assert "MODO COACH" in llm.calls[1]["system"]
    with pytest.raises(ValueError):
        brain.set_mode(42, "pirata")


async def test_resposta_em_streaming_com_rotas_e_telemetria(backend):
    class Streamer:
        name = "s"

        def __init__(self):
            self.calls = 0

        async def chat_stream(self, system, messages, tools=None, temperature=0.9):
            self.calls += 1
            yield ("text", "O admin ")
            yield ("text", "carregou.")
            yield ("done", LLMResponse(text="O admin carregou.", provider="s"))

    voz, texto = Streamer(), Scripted([])
    tel = Telemetry()
    brain = Brain(texto, backend.botapi, routes={"voz": voz}, telemetry=tel)
    got = []

    async def on_text(t, step):
        got.append(t)
    out = await brain.answer("v", "admin", "quem?", voice=True, on_text=on_text)
    assert out == "O admin carregou." and got == ["O admin ", "carregou."] and voz.calls == 1
    s = tel.answer_summary("voz")
    assert s["n"] == 1 and s["sem_tool_pct"] == 100 and s["provedores"] == ["s"]


async def test_aviso_de_cota_chega_ao_usuario(backend):
    from x9.llm import WAIT_NOTICE

    class Waits:
        name = "w"

        async def chat(self, system, messages, tools=None, temperature=0.9):
            await WAIT_NOTICE.get()(7.0)
            return LLMResponse(text="ok", provider="w")
    avisos = []

    async def on_wait(s):
        avisos.append(s)
    brain = Brain(Waits(), backend.botapi)
    assert await brain.answer("k", "admin", "oi", on_wait=on_wait) == "ok"
    assert avisos == [7.0] and WAIT_NOTICE.get() is None


async def test_posts_proativos_usam_dados_reais(backend):
    llm = Scripted([text("preleção"), text("oscar")])
    brain = Brain(llm, backend.botapi)
    assert await brain.prefila([ADMIN_PUUID, FRIEND_PUUID]) == "preleção"
    assert '"jogadores": ["admin", "amigo"]' in llm.calls[0]["messages"][0]["content"]
    assert await brain.night_recap() is None              # fixtures são de meses atrás
    assert await brain.oscar() is None                    # nenhuma partida na semana
    with pytest.raises(BrainUnavailable):
        await Brain(Scripted([LLMError("x")]), backend.botapi).prefila([ADMIN_PUUID])


# ---------------------------------------------------------------------------
# Momentos proativos
# ---------------------------------------------------------------------------
async def test_prefila_espera_o_stack_e_respeita_o_intervalo():
    now = [1000.0]
    fired = []

    async def on_prefila(pus):
        fired.append(pus)
    m = Moments(on_prefila=on_prefila, clock=lambda: now[0], prefila_delay_s=90,
                prefila_cooldown_s=3600)
    m.on_presence("a", True)
    assert await m.tick() == []
    now[0] += 30
    m.on_presence("b", True)                              # 2º abriu: agenda para +90 s
    now[0] += 60
    m.on_presence("c", True)                              # 3º chegou: empurra
    now[0] += 60
    assert await m.tick() == []
    now[0] += 31
    assert await m.tick() == ["prefila"] and fired == [["a", "b", "c"]]
    m.on_presence("c", False)
    m.on_presence("c", True)
    now[0] += 200
    assert await m.tick() == [] and len(fired) == 1       # mesmo grupo: intervalo


async def test_boot_com_gente_jogando_nao_repete_a_prefila():
    now = [1000.0]
    fired = []

    async def on_prefila(pus):
        fired.append(pus)
    m = Moments(on_prefila=on_prefila, clock=lambda: now[0])
    m.on_presence("a", True, quiet=True)
    m.on_presence("b", True, quiet=True)
    now[0] += 600
    assert await m.tick() == [] and fired == [] and m.session_start == 1000.0
    m.on_presence("c", True)                              # alguém abriu de verdade
    now[0] += 91
    assert await m.tick() == ["prefila"] and fired == [["a", "b", "c"]]


async def test_fim_da_noite_e_oscar_uma_vez_por_semana():
    now = [0.0]
    ended, oscars = [], []
    store = {}

    class Store:
        def get_config(self, k):
            return store.get(k)

        def set_config(self, k, v):
            store[k] = v

    async def on_night(start):
        ended.append(start)

    async def on_oscar():
        oscars.append(1)
    sunday = datetime(2026, 10, 18, 22, 30)              # domingo
    m = Moments(on_night_end=on_night, on_oscar=on_oscar, store=Store(),
                clock=lambda: now[0], now=lambda: sunday, night_idle_s=600)
    m.on_presence("a", True)
    now[0] = 100
    m.on_presence("a", False)
    now[0] = 650
    fired = await m.tick()
    assert "noite" not in fired and "oscar" in fired and store[OSCAR_KEY] == "2026-W42"
    now[0] = 701
    assert await m.tick() == ["noite"] and ended == [0.0]
    assert await m.tick() == [] and oscars == [1]
    again = Moments(on_oscar=on_oscar, store=Store(), now=lambda: sunday)
    assert await again.tick() == []                       # reiniciou: o banco lembra


# ---------------------------------------------------------------------------
# Mensagem ao vivo e telemetria
# ---------------------------------------------------------------------------
async def test_mensagem_ao_vivo_posta_cedo_edita_com_freio_e_fecha():
    now = [0.0]
    sent, edits = [], []

    async def send(t):
        sent.append(t)
        return len(sent)

    async def edit(msg, t):
        edits.append((msg, t))
    lm = LiveMessage(send, edit, clock=lambda: now[0], min_interval=1.0, first_chars=10)
    await lm.on_text("Vou ver.", 0)
    await lm.on_text("O admin carregou ", 1)              # passo novo: descarta o preâmbulo
    assert sent == ["O admin carregou" + CURSOR]
    await lm.on_text("com 27 kills", 1)
    await asyncio.sleep(0)
    assert edits == []                                    # freio de 1 s
    now[0] = 1.5
    await lm.on_text(" e 367 de ACS.", 1)
    await asyncio.sleep(0)
    assert edits[-1][1].endswith(CURSOR)
    final = "Final. " + "x" * 2500
    await lm.finish(final)
    from x9.brain import split_message
    assert edits[-1][0] == 1 and not edits[-1][1].endswith(CURSOR)
    assert len(sent) == len(split_message(final))         # 1ª editada, o resto em mensagens novas


async def test_mensagem_ao_vivo_sem_texto_cedo_manda_tudo_no_fim():
    sent = []

    async def send(t):
        sent.append(t)
        return t
    lm = LiveMessage(send, None, first_chars=50)
    await lm.on_text("curto", 0)
    await lm.finish("curto")
    assert sent == ["curto"]


def test_telemetria_percentis_e_partidas():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile([], 0.5) is None
    t = Telemetry()
    for ms, tools in ((1000, ()), (3000, ("partida",)), (2000, ())):
        t.record_answer("texto", ms, {"steps": 1, "provider": "gemini", "tools": tools})
    s = t.answer_summary("texto")
    assert s["p50_s"] == 2.0 and s["sem_tool_pct"] == 67 and t.answer_summary("voz") is None
    m = match_summary([{"ended_raw": 0, "posted_raw": 120, "gossip_raw": 128, "claimed_by": "bot"},
                       {"ended_raw": 0, "posted_raw": 240, "gossip_raw": -5, "claimed_by": "poller"}])
    assert m["placar_p50_min"] == 3.0 and m["fofoca_p50_s"] == 8 and m["pelo_bot_pct"] == 50
    assert match_summary([]) is None


async def test_status_do_bot(backend):
    pytest.importorskip("discord")
    from types import SimpleNamespace

    from x9.discord_bot import X9Bot
    from x9.llm import FallbackProvider
    tel = Telemetry()
    tel.record_answer("texto", 1500, {"tools": []})
    tel.record_voice(2400)
    brain = Brain(FallbackProvider([Scripted([])]), backend.botapi, telemetry=tel)
    bot = X9Bot(brain, backend, SimpleNamespace(device="cpu"), telemetry=tel)
    try:
        backend.botapi.call_tool("resumo_partida", {})
        out = bot.status_text()
        assert "Respostas (texto)" in out and "Voz" in out and "LLM" in out and "Cache" in out
    finally:
        await bot.close()


async def test_comandos_sem_ia(backend):
    pytest.importorskip("discord")
    from types import SimpleNamespace

    from x9.discord_bot import X9Bot
    bot = X9Bot(Brain(Scripted([]), backend.botapi), backend, SimpleNamespace(device="cpu"))
    sent = []

    async def defer(**kw):
        pass

    async def fsend(content=None, **kw):
        sent.append(content)

    async def rsend(content=None, **kw):
        sent.append(content)

    def inter():
        return SimpleNamespace(user=SimpleNamespace(id=int(ADMIN_DISCORD), mention="@a",
                                                    guild_permissions=SimpleNamespace(manage_guild=True)),
                               response=SimpleNamespace(defer=defer, send_message=rsend),
                               followup=SimpleNamespace(send=fsend), channel=SimpleNamespace(id=5),
                               channel_id=5)
    cmd = lambda n: bot.tree.get_command("x9").get_command(n).callback  # noqa: E731
    try:
        await cmd("elo")(inter())
        await cmd("ranking")(inter())
        await cmd("fila")(inter(), "admin, amigo")
        await cmd("hoje")(inter())
        await cmd("aposta")(inter(), SimpleNamespace(name="vitória", value="vitoria"))
        await cmd("apostas")(inter())
        await cmd("modo")(inter(), SimpleNamespace(value="narrador"))
        await cmd("lore")(inter())
        assert "Elo agora" in sent[0] and "Ranking" in sent[1] and "Pré-fila" in sent[2]
        assert "Ninguém jogou" in sent[3] and "apostou em **vitória**" in sent[4]
        assert "vitoria" in sent[5] and "narrador" in sent[6] and "Lore" in sent[7]
        assert bot.brain.mode_for(5) == "narrador"
    finally:
        await bot.close()


async def test_resenha_so_com_partidas_da_sessao(backend, monkeypatch):
    llm = Scripted([text("resenha")])
    brain = Brain(llm, backend.botapi)
    night = {"tem_noite": True, "partidas": 3, "fim_raw": 10_000}
    monkeypatch.setattr(backend.botapi, "material_noite", lambda: dict(night))
    assert await brain.night_recap(since=20_000) is None      # abriu e não jogou
    assert await brain.night_recap(since=10_500) == "resenha"
    night["partidas"] = 1
    assert await brain.night_recap(since=10_500) is None      # uma partida não é noite


def test_config_do_bot_limpa_bom(monkeypatch):
    from x9 import config
    monkeypatch.setenv("X9_TESTE_TOKEN", "﻿abc ")
    assert config._env("X9_TESTE_TOKEN") == "abc"
