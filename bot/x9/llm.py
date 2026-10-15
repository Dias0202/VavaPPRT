"""LLM com tools: Gemini (principal) e Groq (fallback), via HTTP puro.

Formato interno de mensagem (independente do provedor):
    {"role": "user", "content": str}
    {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "args"}],
     "raw": {"gemini": [...parts]}}          # raw opcional, para ecoar ao mesmo provedor
    {"role": "tool", "tool_call_id": str, "name": str, "content": dict}

Os dois provedores entendem esse formato, então trocar de um para o outro no
meio de uma conversa (Gemini estourou a cota) não perde o histórico.

Os dois também falam em **streaming** (`chat_stream`): eventos ("text", trecho)
enquanto o modelo escreve e ("done", LLMResponse) no fim. É o que deixa o X9
mandar a 1ª frase (e falar a 1ª frase na call) antes de a resposta acabar.
"""
import asyncio
import contextvars
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx

# Avisado antes de o FallbackProvider esperar a cota por minuto voltar:
# async fn(segundos). O Brain liga isto por pergunta para o X9 dizer
# "tô no limite da cota, já respondo" em vez de ficar mudo.
WAIT_NOTICE = contextvars.ContextVar("x9_wait_notice", default=None)

logger = logging.getLogger("x9.llm")

MAX_TOOL_RESULT_CHARS = 8000


class LLMError(Exception):
    def __init__(self, msg, retriable=True, retry_after=None):
        super().__init__(msg)
        self.retriable = retriable
        self.retry_after = retry_after  # segundos que o provedor pediu para esperar


_RETRY_PATTERNS = (re.compile(r"try again in ([\d.]+)\s*(ms|s)(?![a-z])", re.I),
                   re.compile(r'"retryDelay":\s*"([\d.]+)(s)"'))


def _retry_after(resp):
    """Quanto o 429 pediu para esperar: header Retry-After, texto do Groq
    ("try again in 8.2s") ou RetryInfo do Gemini ("retryDelay": "8s")."""
    h = resp.headers.get("retry-after")
    try:
        if h:
            return float(h)
    except ValueError:
        pass
    for pat in _RETRY_PATTERNS:
        m = pat.search(resp.text or "")
        if m:
            v = float(m.group(1))
            return v / 1000 if m.group(2).lower() == "ms" else v
    return None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "args"}]
    raw: dict = field(default_factory=dict)
    provider: str = ""


def _tool_json(content):
    s = json.dumps(content, ensure_ascii=False, default=str)
    if len(s) > MAX_TOOL_RESULT_CHARS:
        s = s[:MAX_TOOL_RESULT_CHARS] + "…(cortado)"
    return s


def _tool_payload(content):
    """O objeto em si quando cabe; senão o JSON cortado como texto."""
    s = _tool_json(content)
    return content if len(s) <= MAX_TOOL_RESULT_CHARS else s


async def _sse(resp):
    """Objetos JSON de um corpo text/event-stream ("data: {...}")."""
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except ValueError:
            continue


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Tom "sem filtro" decidido no design: zoeira entre amigos é assédio para o
# classificador. Discurso de ódio continua barrado (e proibido no prompt).
GEMINI_SAFETY = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]


def _gemini_schema(schema):
    """JSON Schema -> Schema OpenAPI do Gemini (tipos em MAIÚSCULAS)."""
    out = {}
    for k, v in schema.items():
        if k == "type":
            out["type"] = str(v).upper()
        elif k == "properties":
            out["properties"] = {n: _gemini_schema(p) for n, p in v.items()}
        elif k == "items":
            out["items"] = _gemini_schema(v)
        elif k == "required":
            if v:
                out["required"] = list(v)
        elif k in ("description", "enum"):
            out[k] = v
    return out


def gemini_tools(catalog):
    decls = []
    for t in catalog:
        d = {"name": t["name"], "description": t["description"]}
        # OBJECT sem propriedades é rejeitado pelo Gemini: tool sem parâmetro
        # vai sem o campo `parameters`.
        if t["parameters"].get("properties"):
            d["parameters"] = _gemini_schema(t["parameters"])
        decls.append(d)
    return [{"functionDeclarations": decls}] if decls else []


def gemini_contents(messages):
    contents = []

    def push(role, parts):
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": list(parts)})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"text": m["content"]}])
        elif m["role"] == "assistant":
            parts = (m.get("raw") or {}).get("gemini")
            if not parts:
                parts = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                parts += [{"functionCall": {"name": c["name"], "args": c["args"]}}
                          for c in m.get("tool_calls") or []]
            push("model", parts)
        elif m["role"] == "tool":
            push("user", [{"functionResponse": {"name": m["name"],
                                                "response": {"result": _tool_payload(m["content"])}}}])
    return contents


_GEMINI_STREAM_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                      "{model}:streamGenerateContent?alt=sse")


def _gemini_status_error(r, text):
    if r.status_code == 429 or r.status_code >= 500:
        return LLMError(f"gemini HTTP {r.status_code}: {text[:200]}",
                        retry_after=_retry_after(r) if r.status_code == 429 else None)
    return LLMError(f"gemini HTTP {r.status_code}: {text[:300]}", retriable=True)


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key, model, client=None, timeout=40, thinking_budget=0):
        self.api_key, self.model = api_key, model
        self.thinking_budget = thinking_budget
        self.client = client or httpx.AsyncClient(timeout=timeout)

    def _body(self, system, messages, tools, temperature):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": gemini_contents(messages),
            "safetySettings": GEMINI_SAFETY,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
        }
        if "flash" in self.model:
            # O 2.5 Flash "pensa" antes de cada passo por padrão: segundos a mais
            # por chamada, e o loop de tools faz várias. Zoeira não precisa disso.
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        if tools:
            body["tools"] = gemini_tools(tools)
        return body

    async def chat_stream(self, system, messages, tools=None, temperature=0.9):
        body = self._body(system, messages, tools, temperature)
        parts, text = [], []
        try:
            async with self.client.stream("POST", _GEMINI_STREAM_URL.format(model=self.model),
                                          json=body,
                                          headers={"x-goog-api-key": self.api_key}) as r:
                if r.status_code >= 400:
                    raise _gemini_status_error(r, (await r.aread()).decode("utf-8", "replace"))
                finish = None
                async for data in _sse(r):
                    cands = data.get("candidates") or []
                    if not cands:
                        if (data.get("promptFeedback") or {}).get("blockReason"):
                            raise LLMError(f"gemini bloqueou: {data['promptFeedback']['blockReason']}")
                        continue
                    cand = cands[0]
                    finish = cand.get("finishReason") or finish
                    for p in (cand.get("content") or {}).get("parts") or []:
                        if p.get("text") and not p.get("thought"):
                            text.append(p["text"])
                            if parts and set(parts[-1]) == {"text"} and set(p) == {"text"}:
                                parts[-1] = {"text": parts[-1]["text"] + p["text"]}
                            else:
                                parts.append(dict(p))
                            yield ("text", p["text"])
                        elif not p.get("thought"):
                            parts.append(p)
        except httpx.HTTPError as e:
            raise LLMError(f"gemini rede: {e}") from e
        calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": p["functionCall"]["name"],
                  "args": p["functionCall"].get("args") or {}}
                 for p in parts if p.get("functionCall")]
        if not parts and finish not in (None, "STOP"):
            raise LLMError(f"gemini parou: {finish}")
        yield ("done", LLMResponse(text="".join(text).strip(), tool_calls=calls,
                                   raw={"gemini": parts}, provider=self.name))

    async def chat(self, system, messages, tools=None, temperature=0.9):
        body = self._body(system, messages, tools, temperature)
        try:
            r = await self.client.post(_GEMINI_URL.format(model=self.model), json=body,
                                       headers={"x-goog-api-key": self.api_key})
        except httpx.HTTPError as e:
            raise LLMError(f"gemini rede: {e}") from e
        if r.status_code >= 400:
            raise _gemini_status_error(r, r.text)
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError(f"gemini sem resposta ({(data.get('promptFeedback') or {}).get('blockReason')})")
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        if not parts and cand.get("finishReason") not in (None, "STOP"):
            raise LLMError(f"gemini parou: {cand.get('finishReason')}")
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": p["functionCall"]["name"],
                  "args": p["functionCall"].get("args") or {}}
                 for p in parts if p.get("functionCall")]
        return LLMResponse(text=text.strip(), tool_calls=calls, raw={"gemini": parts},
                           provider=self.name)


# ---------------------------------------------------------------------------
# Groq (API compatível com OpenAI)
# ---------------------------------------------------------------------------
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _nullable_optionals(schema):
    """O Groq valida a chamada no servidor e o modelo às vezes manda null num
    parâmetro opcional ("jogador": null) -> 400. Opcional passa a aceitar null
    (a botapi já descarta None)."""
    req = set(schema.get("required") or [])
    props = {}
    for name, p in (schema.get("properties") or {}).items():
        p = dict(p)
        if name not in req and isinstance(p.get("type"), str):
            p["type"] = [p["type"], "null"]
            if "enum" in p:
                p["enum"] = list(p["enum"]) + [None]
        props[name] = p
    return {**schema, "properties": props}


def openai_tools(catalog):
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": _nullable_optionals(t["parameters"])}}
            for t in catalog]


def openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"],
                                                   "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                                     for c in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": _tool_json(m["content"])})
    return out


class OpenAICompatProvider:
    # O Groq reserva o max_tokens inteiro na cota por minuto (8000 TPM no free
    # tier) a cada chamada: com 2048, só cabiam ~2 passos do loop por minuto.
    # Resposta do X9 é curta (voz: 3 frases; texto: ~12 linhas).
    MAX_TOKENS = 800

    def __init__(self, api_key, model, url=_GROQ_URL, name="groq", client=None, timeout=40):
        self.api_key, self.model, self.url, self.name = api_key, model, url, name
        self.client = client or httpx.AsyncClient(timeout=timeout)

    def _body(self, system, messages, tools, temperature):
        body = {"model": self.model, "messages": openai_messages(system, messages),
                "temperature": temperature, "max_tokens": self.MAX_TOKENS}
        if self.model.startswith("openai/gpt-oss"):
            # Raciocínio "medium" (padrão) gasta tokens de saída — tempo e cota de
            # TPM do free tier — em toda chamada do loop de tools.
            body["reasoning_effort"] = "low"
        if tools:
            body["tools"] = openai_tools(tools)
            body["tool_choice"] = "auto"
        return body

    def _status_error(self, r, text):
        retry = _retry_after(r) if r.status_code == 429 else None
        if r.status_code == 400 and "tool_use_failed" in text:
            retry = 0.0  # geração de tool inválida: sortear de novo costuma resolver
        return LLMError(f"{self.name} HTTP {r.status_code}: {text[:300]}", retry_after=retry)

    async def chat_stream(self, system, messages, tools=None, temperature=0.9):
        body = {**self._body(system, messages, tools, temperature), "stream": True}
        text, calls = [], {}
        try:
            async with self.client.stream("POST", self.url, json=body,
                                          headers={"Authorization": f"Bearer {self.api_key}"}) as r:
                if r.status_code >= 400:
                    raw = (await r.aread()).decode("utf-8", "replace")
                    # _retry_after lê .headers e .text
                    raise self._status_error(SimpleNamespace(headers=r.headers, text=raw,
                                                             status_code=r.status_code), raw)
                async for data in _sse(r):
                    if data.get("error"):
                        raise LLMError(f"{self.name} stream: {str(data['error'])[:200]}")
                    delta = ((data.get("choices") or [{}])[0].get("delta")) or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                        yield ("text", delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", len(calls)),
                                             {"id": None, "name": "", "arguments": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] += fn.get("name") or ""
                        c["arguments"] += fn.get("arguments") or ""
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} rede: {e}") from e
        out = []
        for _, c in sorted(calls.items()):
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            out.append({"id": c["id"] or f"call_{uuid.uuid4().hex[:8]}", "name": c["name"],
                        "args": args})
        yield ("done", LLMResponse(text="".join(text).strip(), tool_calls=out, provider=self.name))

    async def chat(self, system, messages, tools=None, temperature=0.9):
        body = self._body(system, messages, tools, temperature)
        try:
            r = await self.client.post(self.url, json=body,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} rede: {e}") from e
        if r.status_code >= 400:
            raise self._status_error(r, r.text)
        msg = (r.json().get("choices") or [{}])[0].get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                          "name": fn.get("name"), "args": args})
        return LLMResponse(text=(msg.get("content") or "").strip(), tool_calls=calls,
                           provider=self.name)


def GroqProvider(api_key, model, client=None, timeout=40, name="groq"):
    return OpenAICompatProvider(api_key, model, name=name, client=client, timeout=timeout)


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------
class ProviderHealth:
    """Castigo dos provedores, compartilhado entre as rotas (texto, voz,
    fofoca): cota estourada num é cota estourada para todas."""

    def __init__(self, cooldown_s=60, max_cooldown_s=1800, clock=time.monotonic):
        self.cooldown_s, self.max_cooldown_s, self.clock = cooldown_s, max_cooldown_s, clock
        self.bench, self.strikes = {}, {}
        self.last_error = {}

    def ready(self, p):
        return self.bench.get(p.name, 0) <= self.clock()

    def punish(self, p, err=None):
        n = self.strikes[p.name] = self.strikes.get(p.name, 0) + 1
        self.bench[p.name] = self.clock() + min(self.cooldown_s * 2 ** (n - 1), self.max_cooldown_s)
        if err:
            self.last_error[p.name] = str(err)[:160]

    def short(self, p, err=None):
        """Limite por minuto: castigo curto, sem escalar."""
        self.bench[p.name] = self.clock() + self.cooldown_s
        if err:
            self.last_error[p.name] = str(err)[:160]

    def pardon(self, p):
        self.bench.pop(p.name, None)
        self.strikes.pop(p.name, None)

    def status(self, names):
        now = self.clock()
        return [{"provedor": n, "ok": self.bench.get(n, 0) <= now,
                 "volta_em_s": max(0, int(self.bench.get(n, 0) - now)),
                 "falhas_seguidas": self.strikes.get(n, 0),
                 "ultimo_erro": self.last_error.get(n)} for n in names]


class FallbackProvider:
    """Tenta cada provedor em ordem. Quem falha fica de castigo por `cooldown_s`,
    dobrando a cada falha seguida até `max_cooldown_s` (cota diária estourada
    não volta em 1 minuto, e cada tentativa custa uma ida e volta)."""

    name = "fallback"

    MAX_WAIT_S = 20        # limite por minuto (TPM) pede espera curta: vale aguardar
    MAX_TOTAL_WAIT_S = 45  # mas a resposta não pode demorar para sempre

    def __init__(self, providers, cooldown_s=60, clock=time.monotonic, sleep=asyncio.sleep,
                 max_cooldown_s=1800, health=None):
        if not providers:
            raise ValueError("Nenhum provedor de LLM configurado.")
        self.providers = providers
        self.health = health or ProviderHealth(cooldown_s, max_cooldown_s, clock)
        self.sleep = sleep

    def _order(self):
        ready = [p for p in self.providers if self.health.ready(p)]
        return ready or self.providers  # todo mundo de castigo: tenta mesmo assim

    def _failed(self, p, e, errors, waits, again=False):
        logger.warning("LLM %s falhou: %s", p.name, str(e)[:200])
        errors.append(f"{p.name}{' (de novo)' if again else ''}: {e}")
        short = e.retry_after is not None and e.retry_after <= self.MAX_WAIT_S
        if short:
            self.health.short(p, e)
            waits.append((e.retry_after, p))
        elif not again:
            self.health.punish(p, e)

    async def _retry_waits(self, waits, errors, system, messages, tools, temperature):
        """Todo mundo falhou, mas alguém só pediu uns segundos (limite por
        minuto do free tier): espera e tenta esse de novo."""
        waited = 0.0
        for _ in range(3):
            if not waits:
                break
            wait, p = min(waits, key=lambda w: w[0])
            if waited + wait > self.MAX_TOTAL_WAIT_S:
                break
            logger.info("Esperando %.1fs pelo %s (limite por minuto).", wait, p.name)
            notice = WAIT_NOTICE.get()
            if notice and wait >= 2:
                try:
                    await notice(wait)
                except Exception:
                    logger.exception("Aviso de espera falhou")
            await self.sleep(wait + 0.5)
            waited += wait + 0.5
            waits = []
            try:
                resp = await p.chat(system, messages, tools, temperature)
                self.health.pardon(p)
                return resp
            except LLMError as e:
                self._failed(p, e, errors, waits, again=True)
        raise LLMError(" | ".join(errors))

    async def chat(self, system, messages, tools=None, temperature=0.9):
        errors, waits = [], []
        for p in self._order():
            try:
                resp = await p.chat(system, messages, tools, temperature)
                self.health.pardon(p)
                return resp
            except LLMError as e:
                self._failed(p, e, errors, waits)
        return await self._retry_waits(waits, errors, system, messages, tools, temperature)

    async def chat_stream(self, system, messages, tools=None, temperature=0.9):
        """Como chat(), em streaming. Troca de provedor só se o erro vier antes
        do 1º trecho de texto (depois disso o usuário já está lendo)."""
        errors, waits = [], []
        for p in self._order():
            if not hasattr(p, "chat_stream"):
                try:
                    resp = await p.chat(system, messages, tools, temperature)
                except LLMError as e:
                    self._failed(p, e, errors, waits)
                    continue
                self.health.pardon(p)
                if resp.text and not resp.tool_calls:
                    yield ("text", resp.text)
                yield ("done", resp)
                return
            started = False
            try:
                async for ev in p.chat_stream(system, messages, tools, temperature):
                    if ev[0] == "text":
                        started = True
                    elif ev[0] == "done":
                        self.health.pardon(p)
                    yield ev
                return
            except LLMError as e:
                if started:
                    self.health.punish(p, e)
                    raise
                self._failed(p, e, errors, waits)
        resp = await self._retry_waits(waits, errors, system, messages, tools, temperature)
        if resp.text and not resp.tool_calls:
            yield ("text", resp.text)
        yield ("done", resp)

    def status(self):
        return self.health.status([p.name for p in self.providers])


# ---------------------------------------------------------------------------
# Loop de tools
# ---------------------------------------------------------------------------
async def _step(provider, system, msgs, catalog, temperature, on_text, step):
    if on_text is None or not hasattr(provider, "chat_stream"):
        resp = await provider.chat(system, msgs, catalog, temperature=temperature)
        if on_text is not None and resp.text and not resp.tool_calls:
            await on_text(resp.text, step)
        return resp
    resp = None
    async for kind, val in provider.chat_stream(system, msgs, catalog, temperature):
        if kind == "text":
            await on_text(val, step)
        else:
            resp = val
    return resp or LLMResponse()


async def run_tools_loop(provider, system, messages, catalog, executor, max_steps=6,
                         on_text=None, temperature=0.9, stats=None):
    """Conversa até o modelo responder em texto. Devolve (texto, trilha)
    onde trilha = [(nome, args, resultado)] das tools chamadas.

    on_text: async fn(trecho, passo) — recebe o texto em streaming. Um passo
    que termina chamando tools pode ter escrito algo antes ("Vou ver."): o
    consumidor descarta o texto de passos anteriores quando o passo muda.
    stats: dict que recebe passos, llm_ms, tools_ms, provedor e tools."""
    msgs = list(messages)
    trail = []
    st = stats if stats is not None else {}
    st.update({"steps": 0, "llm_ms": 0.0, "tools_ms": 0.0, "provider": None, "tools": []})
    clock = time.perf_counter
    for step in range(max_steps):
        t0 = clock()
        resp = await _step(provider, system, msgs, catalog, temperature, on_text, step)
        st["llm_ms"] += (clock() - t0) * 1000
        st["steps"] += 1
        st["provider"] = resp.provider or st["provider"]
        if not resp.tool_calls:
            if resp.text:
                return resp.text, trail
            break
        msgs.append({"role": "assistant", "content": resp.text or None,
                     "tool_calls": resp.tool_calls,
                     "raw": resp.raw if resp.provider == "gemini" else {}})
        t0 = clock()
        results = await asyncio.gather(*(executor(c["name"], c["args"]) for c in resp.tool_calls))
        st["tools_ms"] += (clock() - t0) * 1000
        for c, res in zip(resp.tool_calls, results):
            trail.append((c["name"], c["args"], res))
            st["tools"].append(c["name"])
            msgs.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                         "content": res})
    # Estourou os passos (ou veio vazio): força uma resposta sem tools.
    msgs.append({"role": "user", "content": "Responda agora com o que já tem, sem chamar ferramentas."})
    t0 = clock()
    resp = await _step(provider, system, msgs, None, temperature, on_text, max_steps)
    st["llm_ms"] += (clock() - t0) * 1000
    st["steps"] += 1
    return resp.text or "…", trail
