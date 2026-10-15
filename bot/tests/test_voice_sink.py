"""Segmentação de falas no sink de voz (sem Discord de verdade)."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("discord.ext.voice_recv")

from x9.voice import MAX_UTTERANCE_S, SILENCE_S, UtteranceSink  # noqa: E402

FRAME = b"\x01\x00" * 1920  # 20 ms de 48 kHz estéreo s16


def user(uid, bot=False):
    return SimpleNamespace(id=uid, bot=bot, display_name=f"u{uid}")


def feed(sink, u, seconds):
    for _ in range(int(seconds / 0.02)):
        sink.write(u, SimpleNamespace(pcm=FRAME))


def test_fala_termina_no_silencio():
    t = [0.0]
    sink = UtteranceSink(clock=lambda: t[0])
    ana, beto = user(1), user(2)
    feed(sink, ana, 1.0)
    feed(sink, beto, 0.2)          # curta demais: vira ruído
    assert sink.pop_finished() == []  # ainda sem silêncio
    t[0] = SILENCE_S + 0.01
    out = sink.pop_finished()
    assert [m.id for m, _, _ in out] == [1]
    assert len(out[0][1]) == len(FRAME) * 50 and out[0][2] == 1
    assert sink.pop_finished() == []  # descartou a curta e limpou


def test_fala_longa_e_cortada_e_bot_ignorado():
    t = [0.0]
    sink = UtteranceSink(clock=lambda: t[0])
    feed(sink, user(1), MAX_UTTERANCE_S + 0.1)
    feed(sink, user(9, bot=True), 2)
    sink.write(None, SimpleNamespace(pcm=FRAME))
    out = sink.pop_finished()  # sem silêncio, mas passou do máximo
    assert [m.id for m, _, _ in out] == [1]


def test_amostra_do_comeco_da_fala_para_o_wake_antecipado():
    t = [0.0]
    sink = UtteranceSink(clock=lambda: t[0], probe_s=1.0)
    ana = user(1)
    feed(sink, ana, 0.5)
    assert sink.pop_probes() == []            # curta ainda
    feed(sink, ana, 1.0)
    probes = sink.pop_probes()
    assert [(m.id, seq) for m, _, seq in probes] == [(1, 1)]
    assert len(probes[0][1]) == len(FRAME) * 50  # só o 1º segundo
    assert sink.pop_probes() == []            # uma amostra por fala
    t[0] = SILENCE_S + 0.01
    assert [seq for _, _, seq in sink.pop_finished()] == [1]
    feed(sink, ana, 1.2)                      # fala nova: nova amostra
    assert [seq for _, _, seq in sink.pop_probes()] == [2]
    assert UtteranceSink(probe_s=None).pop_probes() == []


# ---------------------------------------------------------------------------
# Pipeline de resposta falada (X9 v2): aviso, frase a frase, barge-in
# ---------------------------------------------------------------------------
class FakeVC:
    def __init__(self):
        self.played, self.channel = [], SimpleNamespace(id=1, members=[])

    def is_connected(self):
        return True

    def is_playing(self):
        return False

    def stop(self):
        pass

    def play(self, source, after=None):
        self.played.append(source.stream.read())
        after(None)


class FakeSpeaker:
    async def synthesize(self, text):
        await asyncio.sleep(0)
        return text.encode()


class FakeChannel:
    id = 5

    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class StreamingBrain:
    def __init__(self, session=None, cut_after_first=False):
        self.session, self.cut = session, cut_after_first

    async def answer(self, key, name, question, on_text=None, **kw):
        await on_text("Primeira frase aqui. ", 0)
        if self.cut:
            self.session._cut += 1          # alguém chamou o X9 no meio
        await on_text("Segunda frase também. ", 0)
        await on_text("Fim", 0)
        return "Primeira frase aqui. Segunda frase também. Fim"


async def _session(backend, brain_cls=StreamingBrain, **kw):
    from x9.telemetry import Telemetry
    from x9.voice import VoiceSession
    tel = Telemetry()
    s = VoiceSession(FakeVC(), None, None, FakeChannel(), backend, FakeSpeaker(),
                     telemetry=tel, acks=("Pera.",))
    s.brain = brain_cls(s, **kw)
    await s._warm_acks()
    return s, tel


async def test_resposta_falada_frase_a_frase(backend):
    s, tel = await _session(backend)
    member = SimpleNamespace(id=7, display_name="Ana")
    await s._handle(member, "quem carregou?", key=(7, 1))
    await asyncio.sleep(0.01)
    assert s.vc.played[0] == b"Pera."                     # aviso primeiro
    assert s.vc.played[1:] == [b"Primeira frase aqui.", b"Segunda frase tamb\xc3\xa9m.", b"Fim"]
    assert "Ana" in s.text_channel.sent[0] and "Segunda frase" in s.text_channel.sent[0]
    assert tel.voice_summary()["n"] == 1


async def test_barge_in_derruba_o_resto_da_resposta(backend):
    s, _ = await _session(backend, cut_after_first=True)
    await s._handle(SimpleNamespace(id=7, display_name="Ana"), "e aí?", key=(7, 1))
    await asyncio.sleep(0.01)
    spoken = [p for p in s.vc.played if p != b"Pera."]
    assert b"Segunda frase tamb\xc3\xa9m." not in spoken
