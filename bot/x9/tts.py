"""Texto -> fala em pt-BR.

Motores, em ordem:
1. edge-tts (vozes neurais da Microsoft, sem chave). É um endpoint não
   oficial e de tempos em tempos responde 403; quando falha, fica de castigo.
2. Piper (local, ONNX, voz pt_BR-faber-medium). Sempre funciona, soa mais
   robótico. A voz (~63 MB) é baixada na primeira vez.
"""
import asyncio
import io
import logging
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .audio import mp3_to_discord

logger = logging.getLogger("x9.tts")

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def clean_for_speech(text):
    """Tira markdown, links, menções e emoji: o TTS leria tudo literalmente."""
    t = re.sub(r"<@!?\d+>", "", text)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>~|]", "", t)
    t = _EMOJI.sub("", t)
    t = re.sub(r"(\d+)\s*[x×]\s*(\d+)", lambda m: f"{m[1]} a {m[2]}", t)  # placar: "13x8" -> "13 a 8"
    return re.sub(r"\s+", " ", t).strip()


async def edge_synthesize(text, voice="pt-BR-AntonioNeural", rate="+8%"):
    """PCM 48 kHz estéreo via edge-tts."""
    import edge_tts

    mp3 = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk.get("type") == "audio":
            mp3 += chunk["data"]
    if not mp3:
        raise RuntimeError("edge-tts não devolveu áudio")
    return mp3_to_discord(bytes(mp3))


class PiperEngine:
    def __init__(self, voice="pt_BR-faber-medium", models_dir=None):
        self.voice_name = voice
        self.models_dir = Path(models_dir or Path(__file__).resolve().parent.parent / "models" / "piper")
        self._voice = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._voice is None:
            from piper import PiperVoice

            model = self.models_dir / f"{self.voice_name}.onnx"
            if not model.exists():
                from piper.download_voices import download_voice

                logger.info("Baixando a voz do Piper %s…", self.voice_name)
                self.models_dir.mkdir(parents=True, exist_ok=True)
                download_voice(self.voice_name, self.models_dir)
            self._voice = PiperVoice.load(model)
        return self._voice

    def synthesize(self, text):
        """PCM 48 kHz estéreo. Bloqueante: rode em thread."""
        with self._lock:
            voice = self._ensure()
            chunks = list(voice.synthesize(text))
        if not chunks:
            raise RuntimeError("Piper não gerou áudio")
        rate = chunks[0].sample_rate
        pcm16 = np.concatenate([c.audio_int16_array for c in chunks]).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm16.tobytes())
        return mp3_to_discord(buf.getvalue())


class Speaker:
    """Escolhe o motor. engine: auto (edge → piper) | edge | piper."""

    def __init__(self, engine="auto", edge_voice="pt-BR-AntonioNeural", piper=None,
                 bench_s=1800, clock=time.monotonic, edge=edge_synthesize):
        self.engine, self.edge_voice = engine, edge_voice
        self.piper = piper or PiperEngine()
        self.bench_s, self.clock, self._edge = bench_s, clock, edge
        self._edge_bench_until = 0.0
        self.last_engine = None

    async def synthesize(self, text):
        text = clean_for_speech(text)
        if not text:
            raise ValueError("nada para falar")
        if self.engine in ("auto", "edge") and self.clock() >= self._edge_bench_until:
            try:
                pcm = await self._edge(text, self.edge_voice)
                self.last_engine = "edge"
                return pcm
            except Exception as e:
                if self.engine == "edge":
                    raise
                logger.warning("edge-tts falhou (%s); usando Piper por %d min.", e, self.bench_s // 60)
                self._edge_bench_until = self.clock() + self.bench_s
        pcm = await asyncio.to_thread(self.piper.synthesize, text)
        self.last_engine = "piper"
        return pcm

    async def warmup(self):
        if self.engine in ("auto", "piper"):
            await asyncio.to_thread(self.piper._ensure)
