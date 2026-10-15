"""Mensagem que se escreve sozinha (docs/PLANO_X9_V2.md, Fase 2.3).

O LLM manda o texto em pedaços; o X9 posta a 1ª frase assim que ela existe e
edita a mensagem conforme o resto chega — no máximo uma edição por
`min_interval` s (o Discord limita edições) e sem bloquear o streaming: a
edição roda em segundo plano e, se a anterior ainda não voltou, a vez passa.

Não importa discord: recebe `send(text) -> msg` e `edit(msg, text)`.
"""
import asyncio
import time

from .brain import split_message

CURSOR = " ▍"


class LiveMessage:
    def __init__(self, send, edit, *, clock=time.monotonic, min_interval=1.0, first_chars=40,
                 limit=1900):
        self.send, self.edit = send, edit
        self.clock, self.min_interval = clock, min_interval
        self.first_chars, self.limit = first_chars, limit
        self.text, self.step = "", None
        self.msg = None
        self._last = 0.0
        self._task = None
        self.edits = 0

    def _preview(self):
        t = self.text.strip()
        if len(t) > self.limit - len(CURSOR):
            t = t[:self.limit - len(CURSOR) - 1] + "…"
        return t + CURSOR

    async def on_text(self, delta, step=0):
        if step != self.step:
            # passo novo do loop de tools: o texto anterior era só preâmbulo
            self.text, self.step = "", step
        self.text += delta
        if self.msg is None:
            if len(self.text.strip()) >= self.first_chars:
                self.msg = await self.send(self._preview())
                self._last = self.clock()
            return
        if self.clock() - self._last < self.min_interval:
            return
        if self._task and not self._task.done():
            return
        self._last = self.clock()
        self._task = asyncio.create_task(self._edit(self._preview()))

    async def _edit(self, text):
        try:
            await self.edit(self.msg, text)
            self.edits += 1
        except Exception:
            pass  # a edição final corrige

    async def finish(self, final_text):
        """Texto final (pode ser maior que uma mensagem: o resto vai em mensagens novas)."""
        if self._task and not self._task.done():
            try:
                await self._task
            except Exception:
                pass
        parts = split_message(final_text, self.limit)
        sent = []
        if self.msg is None:
            for p in parts:
                sent.append(await self.send(p))
            return sent
        await self.edit(self.msg, parts[0])
        sent.append(self.msg)
        for p in parts[1:]:
            sent.append(await self.send(p))
        return sent
