"""Partidas em tempo (quase) real: presença do Discord, agenda e anúncio na hora."""
import pytest

from conftest import ADMIN_PUUID, FRIEND_PUUID, load_matches
from test_brain_announcer import FakeBrain, FakePoster, Scripted, call, text
from x9.announcer import Announcer
from x9.brain import Brain
from x9.live import LiveTracker

VALORANT_MENU = {"details": "Lobby", "state": "In a Party (2 of 5)", "start": None}
VALORANT_GAME = {"details": "Competitive - Ascent", "state": "In Match", "start": None}


def tracker(backend, clock, **kw):
    seen = []

    async def on_new(ids):
        seen.append(ids)

    kw.setdefault("spawn", lambda coro: coro)  # o "depois" roda junto, para o teste ver
    t = LiveTracker(backend, on_new, clock=lambda: clock[0], **kw)
    t.seen = seen
    t.roster()
    return t


def test_agenda_segue_a_presenca(backend):
    now = [1000.0]
    t = tracker(backend, now, base_every=300, playing_every=120, after_end=(45, 120))
    st = t.state[ADMIN_PUUID]
    assert st.next >= 1020  # boot: checagens espaçadas, sem rajada
    t.on_presence(ADMIN_PUUID, None)                    # fora do jogo: não antecipa nada
    assert st.next >= 1020 and st.burst == []

    st.next = 9999                                      # isola do agendamento do boot
    t.state[FRIEND_PUUID].next = 9999
    t.on_presence(ADMIN_PUUID, VALORANT_MENU)          # abriu o jogo
    assert st.playing and st.next == 1120
    t.on_presence(ADMIN_PUUID, VALORANT_MENU)          # mesma presença: nada muda
    assert st.burst == []
    t.on_presence(ADMIN_PUUID, VALORANT_GAME)          # entrou em partida
    t.on_presence(ADMIN_PUUID, VALORANT_MENU)          # voltou ao menu: partida acabou?
    assert st.burst == [1045, 1120] and st.next == 1045

    now[0] = 1045
    assert t.next_due() == ADMIN_PUUID
    t.after_check(ADMIN_PUUID, found_new=False)        # Henrik ainda não tem
    assert st.next == 1120
    now[0] = 1120
    t.after_check(ADMIN_PUUID, found_new=True)         # achou: volta ao ritmo de quem joga
    assert st.burst == [] and st.next == 1240

    t.on_presence(ADMIN_PUUID, None)                    # fechou o jogo
    assert not st.playing and st.burst == [1165, 1240]
    t.after_check(ADMIN_PUUID, found_new=True)
    assert st.next == 1120 + 300                        # sem jogar: varredura de base


def test_quem_esta_jogando(backend):
    now = [5000.0]
    t = tracker(backend, now)
    t.on_presence(ADMIN_PUUID, {**VALORANT_GAME, "start": 5000 - 25 * 60})
    t.on_presence(FRIEND_PUUID, None)
    t.on_presence(None, VALORANT_GAME)                  # sem vínculo: ignorado
    out = t.playing_now()["jogando_agora"]
    assert out == [{"nome": "admin", "jogando_ha_min": 25,
                    "detalhe": "Competitive - Ascent", "estado": "In Match"}]


def _drop_match(db, mid):
    conn = db.get_connection()
    try:
        ph = "?" if db._is_sqlite(conn) else "%s"
        conn.cursor().execute(f"DELETE FROM matches WHERE match_id = {ph}", (mid,))
        conn.commit()
    finally:
        conn.close()


async def test_tick_baixa_partida_nova_e_avisa(backend, henrik):
    mid = sorted(load_matches(), key=lambda m: m["metadata"]["started_at"])[-1]["metadata"]["match_id"]
    _drop_match(backend.db, mid)                        # "acabou de terminar"
    now = [10_000.0]
    t = tracker(backend, now)
    t.on_presence(ADMIN_PUUID, VALORANT_GAME)
    t.on_presence(ADMIN_PUUID, None)                    # saiu do jogo -> rajada
    now[0] += 45
    henrik.calls.clear()
    got = await t.tick()
    assert got == [mid] and t.seen == [[mid]]
    # placar primeiro, RR depois (antes o RR atrasava o placar)
    assert henrik.calls[0] == "v4" and henrik.calls.index("mmr") > 0
    assert backend.db.match_exists(mid)
    assert t.state[ADMIN_PUUID].burst == []
    assert await t.tick() == []                         # nada vencido: não gasta cota


async def test_anuncio_na_hora(backend):
    mid = backend.db.group_matches(sorted(backend.botapi._Ctx().friends), 1)[0]
    poster, brain = FakePoster(), FakeBrain()
    ann = Announcer(brain, poster, backend, channel_id=777)
    assert await ann.announce_now([mid]) == 1
    assert len(poster.embeds) == 1 and brain.gossiped == [mid]
    assert backend.db.get_announcement(mid)["claimed_by"] == "bot"
    assert await ann.announce_now([mid]) == 0           # claim: nunca posta 2x
    assert len(poster.embeds) == 1


async def test_tool_local_quem_ta_jogando(backend):
    llm = Scripted([call("quem_ta_jogando"), text("o admin tá on")])
    brain = Brain(llm, backend.botapi, local_tools={
        "quem_ta_jogando": (lambda args: {"jogando_agora": [{"nome": "admin"}]}, "d",
                            {"type": "object", "properties": {}, "required": []}),
        "quebrada": (lambda args: 1 / 0, "d", {"type": "object", "properties": {}, "required": []})})
    assert "quem_ta_jogando" in {t["name"] for t in brain._catalog}
    assert await brain.answer("k", "admin", "quem tá on?") == "o admin tá on"
    assert llm.calls[1]["messages"][-1]["content"] == {"jogando_agora": [{"nome": "admin"}]}
    assert (await brain._executor(None)("quebrada", {}))["erro"]


def test_atividade_do_valorant_no_membro():
    pytest.importorskip("discord")
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from x9.discord_bot import X9Bot
    start = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
    m = SimpleNamespace(activities=[SimpleNamespace(name="Spotify"),
                                    SimpleNamespace(name="VALORANT", details="Competitive",
                                                    state="In Match", start=start)])
    assert X9Bot.valorant_activity(m) == {"details": "Competitive", "state": "In Match",
                                          "start": start.timestamp(), "party": None}
    m.activities[1].party = {"id": "p1", "size": [2, 5]}
    assert X9Bot.valorant_activity(m)["party"] == "p1"
    assert X9Bot.valorant_activity(SimpleNamespace(activities=[])) is None
