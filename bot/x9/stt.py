"""Fala -> texto com faster-whisper local (pt-br)."""
import logging
import threading

logger = logging.getLogger("x9.stt")

# Vocabulário que o Whisper erra sem contexto: o próprio nome e o jargão.
INITIAL_PROMPT = ("X9, Valorant, round, clutch, ace, first blood, trade, eco, full buy, "
                  "Jett, Reyna, Raze, Sova, Omen, Sage, Killjoy, Cypher, Chamber, Neon, "
                  "Ascent, Bind, Haven, Split, Lotus, Sunset, Abyss, Icebox, Breeze.")


class Transcriber:
    def __init__(self, model="small", device="auto"):
        self.model_name, self.device = model, device
        self._model = None
        self._lock = threading.Lock()

    def _load(self, device):
        from faster_whisper import WhisperModel

        compute = "float16" if device == "cuda" else "int8"
        logger.info("Carregando Whisper %s (%s/%s)…", self.model_name, device, compute)
        return WhisperModel(self.model_name, device=device, compute_type=compute)

    def _ensure(self):
        if self._model is None:
            if self.device in ("auto", "cuda"):
                try:
                    self._model, self.device = self._load("cuda"), "cuda"
                    return
                except Exception as e:
                    if self.device == "cuda":
                        raise
                    logger.info("Sem CUDA utilizável (%s); usando CPU.", e)
            self._model, self.device = self._load("cpu"), "cpu"

    def warmup(self):
        with self._lock:
            self._ensure()

    def transcribe(self, audio16k):
        """float32 16 kHz mono -> texto. Bloqueante: rode em thread."""
        with self._lock:
            self._ensure()
            try:
                return self._run(audio16k)
            except RuntimeError as e:
                # No Windows a falta das DLLs do cuBLAS só aparece na 1ª transcrição.
                if self.device != "cuda":
                    raise
                logger.warning("CUDA falhou ao transcrever (%s); caindo para CPU.", e)
                self._model, self.device = self._load("cpu"), "cpu"
                return self._run(audio16k)

    def _run(self, audio16k):
        segments, _ = self._model.transcribe(
            audio16k, language="pt", beam_size=1, vad_filter=True,
            initial_prompt=INITIAL_PROMPT, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()
