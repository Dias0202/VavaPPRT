"""Call de voz: ouve (Whisper), reconhece "X9, …", responde falando (edge-tts ou Piper).

O Discord não manda pacote durante o silêncio, então uma fala termina quando o
usuário fica `silence_s` sem pacote. A recepção usa o fork do
discord-ext-voice-recv com suporte a DAVE (E2EE obrigatório desde mar/2026).

X9 v2 (docs/PLANO_X9_V2.md, Fase 3):
- fim de fala em 0,55 s (antes 0,8 s);
- **wake antecipado** (GPU): com 1,2 s de fala, transcreve só o começo; se for
  "X9…", o aviso toca assim que a pessoa para de falar (sem esperar a
  transcrição inteira) e, se o X9 estiver falando, ele cala (**barge-in**);
- **fala por frase**: o LLM responde em streaming, cada frase pronta vai para o
  TTS e a seguinte é sintetizada enquanto a anterior toca;
- avisos variados, pré-sintetizados no começo da sessão.
"""
import asyncio
import io
import logging
import random
import threading
import time
from collections import defaultdict, deque

import discord
from discord.ext import voice_recv

from . import audio, wake
from .brain import UNAVAILABLE_REPLY, BrainUnavailable, SentenceStream

logger = logging.getLogger("x9.voice")

SILENCE_S = 0.55
MIN_UTTERANCE_S = 0.4
MAX_UTTERANCE_S = 15.0
PROBE_S = 1.2

ACK_LINES = ("Deixa eu ver.", "Pera, puxando a ficha.", "Hmm, isso vai doer.", "Já te falo.",
             "Segura aí.", "Consultando os autos.", "Deixa eu abrir o dossiê.",
             "Calma que o X9 tá vendo.", "Ih, lá vem.", "Um segundo, chefe.",
             "Abrindo o arquivo.", "Bora ver isso aí.")


class UtteranceSink(voice_recv.AudioSink):
    """Junta o PCM de cada pessoa até ela parar de falar."""

    def __init__(self, clock=time.monotonic, silence_s=SILENCE_S, probe_s=None):
        super().__init__()
        self.clock, self.silence_s, self.probe_s = clock, silence_s, probe_s
        self._lock = threading.Lock()
        self._buf = defaultdict(bytearray)
        self._last = {}
        self._who = {}
        self._seq = defaultdict(int)
        self._probed = set()

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is None or getattr(user, "bot", False) or not data.pcm:
            return
        with self._lock:
            if not self._buf.get(user.id):
                self._seq[user.id] += 1  # fala nova
            self._buf[user.id] += data.pcm
            self._last[user.id] = self.clock()
            self._who[user.id] = user

    def cleanup(self):
        with self._lock:
            self._buf.clear()

    def seq(self, uid):
        return self._seq.get(uid, 0)

    def forget_probes(self):
        with self._lock:
            self._probed = {(u, n) for u, n in self._probed if self._seq.get(u) == n}

    def pop_probes(self):
        """[(membro, pcm do começo, seq)] de falas em andamento que já têm
        `probe_s` e ainda não foram amostradas (wake antecipado)."""
        if not self.probe_s:
            return []
        n = int(self.probe_s * audio.BYTES_PER_SECOND)
        n -= n % 4
        out = []
        with self._lock:
            for uid, pcm in self._buf.items():
                key = (uid, self._seq[uid])
                if len(pcm) >= n and key not in self._probed:
                    self._probed.add(key)
                    out.append((self._who[uid], bytes(pcm[:n]), key[1]))
        return out

    def pop_finished(self):
        """[(membro, pcm, seq)] das falas que terminaram (silêncio ou tamanho máximo)."""
        now = self.clock()
        out = []
        with self._lock:
            for uid in list(self._buf):
                pcm = self._buf[uid]
                done = now - self._last.get(uid, now) >= self.silence_s
                if done or audio.pcm_seconds(pcm) >= MAX_UTTERANCE_S:
                    if audio.pcm_seconds(pcm) >= MIN_UTTERANCE_S:
                        out.append((self._who[uid], bytes(pcm), self._seq[uid]))
                    del self._buf[uid]
        return out


class VoiceSession:
    """Estágios: o Whisper transcreve todo mundo sem parar e só as falas com
    "X9" vão para a fila de respostas; a resposta é falada frase a frase."""

    def __init__(self, vc, brain, transcriber, text_channel, backend, speaker, *,
                 telemetry=None, early_wake=False, silence_s=SILENCE_S, mode_for=None,
                 acks=ACK_LINES, rng=None):
        self.vc, self.brain, self.transcriber = vc, brain, transcriber
        self.text_channel, self.backend, self.speaker = text_channel, backend, speaker
        self.telemetry, self.mode_for = telemetry, mode_for
        self.sink = UtteranceSink(silence_s=silence_s, probe_s=PROBE_S if early_wake else None)
        self._speak_lock = asyncio.Lock()
        self._heard = asyncio.Queue(maxsize=32)
        self._asked = asyncio.Queue(maxsize=4)
        self._probes = asyncio.Queue(maxsize=8)
        self._wake = set()        # (uid, seq) em que a amostra ouviu "X9"
        self._acked = set()
        self._acks_text = list(acks)
        self._acks = []           # [(texto, pcm)]
        self._recent_acks = deque(maxlen=3)
        self._cut = 0             # barge-in: incrementa e as frases pendentes caem
        self.rng = rng or random.Random()
        self._tasks = []

    def start(self):
        self.vc.listen(self.sink)
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._collect()), loop.create_task(self._transcribe()),
                       loop.create_task(self._answer()), loop.create_task(self._warm_acks())]
        if self.sink.probe_s:
            self._tasks.append(loop.create_task(self._probe()))

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        if self.vc.is_listening():
            self.vc.stop_listening()
        await self.vc.disconnect(force=True)

    def connected(self):
        return self.vc.is_connected()

    @staticmethod
    def _put(queue, item, what):
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            # Fala velha perde para a nova: a conversa já andou.
            queue.get_nowait()
            queue.put_nowait(item)
            logger.warning("Fila de %s cheia; descartei a mais antiga.", what)

    # --- avisos -------------------------------------------------------------
    async def _warm_acks(self):
        for text in self._acks_text:
            try:
                self._acks.append((text, await self.speaker.synthesize(text)))
            except Exception as e:
                logger.warning("Aviso '%s' não sintetizou: %s", text, e)

    async def _ack(self):
        """Resposta imediata enquanto o LLM pensa (avisos sorteados, sem repetir)."""
        try:
            pool = [a for a in self._acks if a[0] not in self._recent_acks] or self._acks
            if pool:
                text, pcm = self.rng.choice(pool)
            else:
                text, pcm = ACK_LINES[0], await self.speaker.synthesize(ACK_LINES[0])
            self._recent_acks.append(text)
            await self.play(pcm)
        except Exception as e:
            logger.warning("Não consegui tocar o aviso: %s", e)

    # --- ouvir --------------------------------------------------------------
    async def _collect(self):
        while True:
            await asyncio.sleep(0.05)
            if len(self._acked) > 2000:  # call longa: esquece as falas antigas
                self._acked.clear()
                self._wake.clear()
                self.sink.forget_probes()
            for member, pcm, seq in self.sink.pop_probes():
                self._put(self._probes, (member, pcm, seq), "amostras")
            for member, pcm, seq in self.sink.pop_finished():
                key = (member.id, seq)
                if key in self._wake and key not in self._acked:
                    # a amostra já ouviu "X9": aviso na hora, sem esperar o Whisper
                    self._acked.add(key)
                    asyncio.create_task(self._ack())
                self._put(self._heard, (member, pcm, seq, time.monotonic()), "fala")

    async def _probe(self):
        while True:
            member, pcm, seq = await self._probes.get()
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe,
                                               audio.discord_to_whisper(pcm))
            except Exception:
                logger.exception("Falha na amostra de %s", member)
                continue
            if text and wake.strip_wake(text) is not None:
                self._wake.add((member.id, seq))
                if self.vc.is_playing():
                    logger.info("Barge-in: %s chamou o X9 enquanto ele falava.", member.display_name)
                    self._cut += 1
                    self.vc.stop()

    async def _transcribe(self):
        while True:
            member, pcm, seq, t_end = await self._heard.get()
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe,
                                               audio.discord_to_whisper(pcm))
            except Exception:
                logger.exception("Falha ao transcrever fala de %s", member)
                continue
            if not text:
                continue
            question = wake.strip_wake(text)
            logger.info("🎙️ %s: %s%s", member.display_name, text, "" if question is None else "  [X9]")
            if question is not None:
                self._put(self._asked, (member, question, (member.id, seq), t_end), "perguntas")

    async def _answer(self):
        while True:
            member, question, key, t_end = await self._asked.get()
            try:
                await self._handle(member, question, key, t_end)
            except Exception:
                logger.exception("Falha ao responder %s", member)

    # --- responder ----------------------------------------------------------
    async def _handle(self, member, question, key=None, t_end=None):
        t_end = t_end or time.monotonic()
        if not question:
            await self.say("Fala, tô ouvindo.")
            return
        if key not in self._acked:
            self._acked.add(key)
            asyncio.create_task(self._ack())
        label = await asyncio.to_thread(self.backend.botapi.linked_label, member.id)
        sentences = asyncio.Queue()
        stream = SentenceStream()
        first = {}
        cut = self._cut

        async def on_text(delta, step):
            for s in stream.feed(delta, step):
                await sentences.put(s)

        speaker = asyncio.create_task(self._speak_sentences(sentences, first, cut))
        mode = self.mode_for(self.text_channel.id) if (self.mode_for and self.text_channel) else None
        try:
            reply = await self.brain.answer(f"voice:{self.vc.channel.id}", label or member.display_name,
                                            question, speaker_discord_id=member.id,
                                            speaker_linked=bool(label), voice=True,
                                            on_text=on_text, mode=mode)
        except BrainUnavailable:
            reply = UNAVAILABLE_REPLY
            stream.buf = ""
            await sentences.put(reply)
        for s in stream.flush():
            await sentences.put(s)
        await sentences.put(None)
        await speaker
        if first.get("t") and self.telemetry:
            self.telemetry.record_voice((first["t"] - t_end) * 1000)
        logger.info("X9 respondeu %s: 1º áudio %.1fs depois da fala, total %.1fs",
                    member.display_name, (first.get("t", time.monotonic()) - t_end),
                    time.monotonic() - t_end)
        if self.text_channel:
            try:
                await self.text_channel.send(f"🎙️ **{member.display_name}:** {question}\n"
                                             f"🕵️ **X9:** {reply}"[:1990])
            except discord.HTTPException:
                pass

    async def _speak_sentences(self, sentences, first, cut):
        """Sintetiza cada frase assim que chega e toca na ordem (a próxima é
        sintetizada enquanto a anterior toca)."""
        synth = asyncio.Queue()

        async def produce():
            while True:
                s = await sentences.get()
                if s is None:
                    await synth.put(None)
                    return
                await synth.put(asyncio.create_task(self.speaker.synthesize(s)))

        producer = asyncio.create_task(produce())
        while True:
            task = await synth.get()
            if task is None:
                break
            try:
                pcm = await task
            except Exception as e:
                logger.warning("Frase não sintetizou: %s", e)
                continue
            if self._cut != cut:
                continue  # alguém chamou o X9 de novo: o resto desta resposta cai
            first.setdefault("t", time.monotonic())
            await self.play(pcm)
        await producer

    async def say(self, text):
        """Fala na call (uma frase por vez; não se atropela)."""
        if not self.connected():
            return False
        return await self.play(await self.speaker.synthesize(text))

    async def play(self, pcm):
        if not self.connected():
            return False
        async with self._speak_lock:
            done = asyncio.Event()
            loop = asyncio.get_running_loop()

            def after(err):
                if err:
                    logger.warning("Erro tocando áudio: %s", err)
                loop.call_soon_threadsafe(done.set)

            if self.vc.is_playing():
                self.vc.stop()
            self.vc.play(discord.PCMAudio(io.BytesIO(pcm)), after=after)
            await done.wait()
        return True
