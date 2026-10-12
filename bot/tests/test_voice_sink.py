"""Segmentação de falas no sink de voz (sem Discord de verdade)."""
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
    assert [m.id for m, _ in out] == [1]
    assert len(out[0][1]) == len(FRAME) * 50
    assert sink.pop_finished() == []  # descartou a curta e limpou


def test_fala_longa_e_cortada_e_bot_ignorado():
    t = [0.0]
    sink = UtteranceSink(clock=lambda: t[0])
    feed(sink, user(1), MAX_UTTERANCE_S + 0.1)
    feed(sink, user(9, bot=True), 2)
    sink.write(None, SimpleNamespace(pcm=FRAME))
    out = sink.pop_finished()  # sem silêncio, mas passou do máximo
    assert [m.id for m, _ in out] == [1]
