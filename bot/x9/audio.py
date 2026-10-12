"""Conversões de áudio sem ffmpeg.

Discord entrega/espera PCM s16le 48 kHz estéreo (frames de 20 ms). O Whisper
quer float32 16 kHz mono. O edge-tts devolve MP3, decodificado com PyAV (que
já vem como dependência do faster-whisper).
"""
import io

import numpy as np

DISCORD_RATE = 48000
BYTES_PER_SECOND = DISCORD_RATE * 2 * 2  # estéreo, 16 bits


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / BYTES_PER_SECOND


def discord_to_whisper(pcm: bytes) -> np.ndarray:
    """PCM 48 kHz estéreo s16 -> float32 16 kHz mono (média de 3 em 3 amostras)."""
    a = np.frombuffer(pcm[: len(pcm) - len(pcm) % 4], dtype=np.int16)
    if a.size == 0:
        return np.zeros(0, dtype=np.float32)
    mono = a.reshape(-1, 2).astype(np.float32).mean(axis=1)
    mono = mono[: mono.size - mono.size % 3].reshape(-1, 3).mean(axis=1)
    return (mono / 32768.0).astype(np.float32)


def rms(pcm: bytes) -> float:
    a = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def mp3_to_discord(data: bytes) -> bytes:
    """MP3 (ou qualquer formato que o PyAV leia) -> PCM 48 kHz estéreo s16."""
    import av

    out = bytearray()
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="stereo", rate=DISCORD_RATE)
        for frame in container.decode(audio=0):
            for f in resampler.resample(frame):
                out += f.to_ndarray().tobytes()
        for f in resampler.resample(None):
            out += f.to_ndarray().tobytes()
    return bytes(out)
