"""Palavra de ativação, conversões de áudio e limpeza de texto para fala."""
import io
import math
import struct
import wave

import numpy as np
import pytest

from x9 import audio
from x9.tts import clean_for_speech
from x9.wake import strip_wake


@pytest.mark.parametrize("text,expected", [
    ("X9, quem carregou ontem?", "quem carregou ontem?"),
    ("x-9 conta o round 13", "conta o round 13"),
    ("Xis nove, quem foi o pior?", "quem foi o pior?"),
    ("Ei X9! Qual foi o placar?", "Qual foi o placar?"),
    ("Fala xisnove, e o Luan?", "e o Luan?"),
    ("chis nove como foi a última", "como foi a última"),
    ("Ô X 9, e aí?", "e aí?"),
    ("xis nova quem carregou", "quem carregou"),     # Whisper errando a vogal
    ("Quem carregou na última, X9?", "Quem carregou na última"),
    ("X9!", ""),
])
def test_chama_o_x9(text, expected):
    assert strip_wake(text) == expected


@pytest.mark.parametrize("text", [
    "quem carregou ontem?",
    "a gente perdeu de 13 a 9",
    "xis da questão é a economia",
    "vamo pro próximo",
    "",
    "existe nove jeitos de perder",
])
def test_nao_chama(text):
    assert strip_wake(text) is None


def test_discord_para_whisper():
    # 1 s de 48 kHz estéreo s16 -> 16000 amostras float32 em [-1, 1]
    t = np.arange(48000)
    wave_ = (np.sin(2 * math.pi * 440 * t / 48000) * 20000).astype(np.int16)
    pcm = np.stack([wave_, wave_], axis=1).tobytes()
    out = audio.discord_to_whisper(pcm)
    assert out.dtype == np.float32 and out.shape == (16000,)
    assert 0.5 < np.abs(out).max() <= 1.0
    assert audio.pcm_seconds(pcm) == pytest.approx(1.0)
    assert audio.discord_to_whisper(b"").size == 0
    assert audio.discord_to_whisper(pcm[:-1]).size > 0  # bytes quebrados não explodem


def test_decodifica_para_pcm_do_discord():
    # WAV 22.05 kHz mono de 0,5 s -> 48 kHz estéreo s16 (~0,5 s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10)))
                               for i in range(11025)))
    pcm = audio.mp3_to_discord(buf.getvalue())
    assert len(pcm) % 4 == 0
    assert audio.pcm_seconds(pcm) == pytest.approx(0.5, abs=0.05)
    assert audio.rms(pcm) > 1000


def test_limpa_texto_para_fala():
    s = clean_for_speech("**Perdemos** de 8x13 no Ascent 😂 <@123> veja [aqui](http://x.y) `ok`")
    assert s == "Perdemos de 8 a 13 no Ascent veja aqui ok"


# ---------------------------------------------------------------------------
# Speaker: edge-tts com castigo, Piper de reserva
# ---------------------------------------------------------------------------
class FakePiper:
    def __init__(self):
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        return b"piper"

    def _ensure(self):
        return self


async def test_speaker_cai_para_piper_e_poe_edge_de_castigo():
    from x9.tts import Speaker
    t = [0.0]
    edge_calls = []

    async def edge(text, voice):
        edge_calls.append(text)
        if len(edge_calls) == 1:
            raise RuntimeError("403")
        return b"edge"

    piper = FakePiper()
    sp = Speaker("auto", piper=piper, edge=edge, bench_s=1800, clock=lambda: t[0])
    assert await sp.synthesize("**oi** 13x2") == b"piper" and sp.last_engine == "piper"
    assert piper.calls == ["oi 13 a 2"]
    assert await sp.synthesize("de novo") == b"piper"  # edge de castigo
    assert len(edge_calls) == 1
    t[0] = 1801
    assert await sp.synthesize("voltou") == b"edge" and sp.last_engine == "edge"


async def test_speaker_motor_fixo():
    from x9.tts import Speaker

    async def edge(text, voice):
        raise RuntimeError("403")

    with pytest.raises(RuntimeError):
        await Speaker("edge", piper=FakePiper(), edge=edge).synthesize("oi")
    piper = FakePiper()
    assert await Speaker("piper", piper=piper, edge=edge).synthesize("oi") == b"piper"
    with pytest.raises(ValueError):
        await Speaker("piper", piper=piper).synthesize("😂")
