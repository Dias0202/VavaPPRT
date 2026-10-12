"""LLM com tools: Gemini (principal) e Groq (fallback), via HTTP puro.

Formato interno de mensagem (independente do provedor):
    {"role": "user", "content": str}
    {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "args"}],
     "raw": {"gemini": [...parts]}}          # raw opcional, para ecoar ao mesmo provedor
    {"role": "tool", "tool_call_id": str, "name": str, "content": dict}

Os dois provedores entendem esse formato, então trocar de um para o outro no
meio de uma conversa (Gemini estourou a cota) não perde o histórico.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx

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


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key, model, client=None, timeout=40):
        self.api_key, self.model = api_key, model
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def chat(self, system, messages, tools=None, temperature=0.9):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": gemini_contents(messages),
            "safetySettings": GEMINI_SAFETY,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
        }
        if "flash" in self.model:
            # O 2.5 Flash "pensa" antes de cada passo por padrão: segundos a mais
            # por chamada, e o loop de tools faz várias. Zoeira não precisa disso.
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        if tools:
            body["tools"] = gemini_tools(tools)
        try:
            r = await self.client.post(_GEMINI_URL.format(model=self.model), json=body,
                                       headers={"x-goog-api-key": self.api_key})
        except httpx.HTTPError as e:
            raise LLMError(f"gemini rede: {e}") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise LLMError(f"gemini HTTP {r.status_code}: {r.text[:200]}",
                           retry_after=_retry_after(r) if r.status_code == 429 else None)
        if r.status_code >= 400:
            raise LLMError(f"gemini HTTP {r.status_code}: {r.text[:300]}", retriable=True)
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

    async def chat(self, system, messages, tools=None, temperature=0.9):
        body = {"model": self.model, "messages": openai_messages(system, messages),
                "temperature": temperature, "max_tokens": self.MAX_TOKENS}
        if self.model.startswith("openai/gpt-oss"):
            # Raciocínio "medium" (padrão) gasta tokens de saída — tempo e cota de
            # TPM do free tier — em toda chamada do loop de tools.
            body["reasoning_effort"] = "low"
        if tools:
            body["tools"] = openai_tools(tools)
            body["tool_choice"] = "auto"
        try:
            r = await self.client.post(self.url, json=body,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} rede: {e}") from e
        if r.status_code >= 400:
            retry = _retry_after(r) if r.status_code == 429 else None
            if r.status_code == 400 and "tool_use_failed" in r.text:
                retry = 0.0  # geração de tool inválida: sortear de novo costuma resolver
            raise LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}", retry_after=retry)
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
class FallbackProvider:
    """Tenta cada provedor em ordem. Quem falha fica de castigo por `cooldown_s`,
    dobrando a cada falha seguida até `max_cooldown_s` (cota diária estourada
    não volta em 1 minuto, e cada tentativa custa uma ida e volta)."""

    name = "fallback"

    MAX_WAIT_S = 20        # limite por minuto (TPM) pede espera curta: vale aguardar
    MAX_TOTAL_WAIT_S = 45  # mas a resposta não pode demorar para sempre

    def __init__(self, providers, cooldown_s=60, clock=time.monotonic, sleep=asyncio.sleep,
                 max_cooldown_s=1800):
        if not providers:
            raise ValueError("Nenhum provedor de LLM configurado.")
        self.providers, self.cooldown_s, self.clock = providers, cooldown_s, clock
        self.max_cooldown_s = max_cooldown_s
        self.sleep = sleep
        self._bench = {}
        self._strikes = {}

    def _punish(self, p):
        n = self._strikes[p.name] = self._strikes.get(p.name, 0) + 1
        self._bench[p.name] = self.clock() + min(self.cooldown_s * 2 ** (n - 1), self.max_cooldown_s)

    def _pardon(self, p):
        self._bench.pop(p.name, None)
        self._strikes.pop(p.name, None)

    async def chat(self, system, messages, tools=None, temperature=0.9):
        now = self.clock()
        ready = [p for p in self.providers if self._bench.get(p.name, 0) <= now]
        order = ready or self.providers  # todo mundo de castigo: tenta mesmo assim
        errors, waits = [], []
        for p in order:
            try:
                resp = await p.chat(system, messages, tools, temperature)
                self._pardon(p)
                return resp
            except LLMError as e:
                logger.warning("LLM %s falhou: %s", p.name, str(e)[:200])
                errors.append(f"{p.name}: {e}")
                if e.retry_after is not None and e.retry_after <= self.MAX_WAIT_S:
                    # limite por minuto: castigo curto, sem escalar
                    self._bench[p.name] = self.clock() + self.cooldown_s
                else:
                    self._punish(p)
                if e.retry_after is not None and e.retry_after <= self.MAX_WAIT_S:
                    waits.append((e.retry_after, p))
        waited = 0.0
        for _ in range(3):
            # Todo mundo falhou, mas alguém só pediu uns segundos (limite por
            # minuto do free tier): espera e tenta esse de novo.
            if not waits:
                break
            wait, p = min(waits, key=lambda w: w[0])
            if waited + wait > self.MAX_TOTAL_WAIT_S:
                break
            logger.info("Esperando %.1fs pelo %s (limite por minuto).", wait, p.name)
            await self.sleep(wait + 0.5)
            waited += wait + 0.5
            waits = []
            try:
                resp = await p.chat(system, messages, tools, temperature)
                self._pardon(p)
                return resp
            except LLMError as e:
                errors.append(f"{p.name} (de novo): {e}")
                if e.retry_after is not None and e.retry_after <= self.MAX_WAIT_S:
                    waits.append((e.retry_after, p))
        raise LLMError(" | ".join(errors))


# ---------------------------------------------------------------------------
# Loop de tools
# ---------------------------------------------------------------------------
async def run_tools_loop(provider, system, messages, catalog, executor, max_steps=6):
    """Conversa até o modelo responder em texto. Devolve (texto, trilha)
    onde trilha = [(nome, args, resultado)] das tools chamadas."""
    msgs = list(messages)
    trail = []
    for _ in range(max_steps):
        resp = await provider.chat(system, msgs, catalog)
        if not resp.tool_calls:
            if resp.text:
                return resp.text, trail
            break
        msgs.append({"role": "assistant", "content": resp.text or None,
                     "tool_calls": resp.tool_calls,
                     "raw": resp.raw if resp.provider == "gemini" else {}})
        results = await asyncio.gather(*(executor(c["name"], c["args"]) for c in resp.tool_calls))
        for c, res in zip(resp.tool_calls, results):
            trail.append((c["name"], c["args"], res))
            msgs.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                         "content": res})
    # Estourou os passos (ou veio vazio): força uma resposta sem tools.
    msgs.append({"role": "user", "content": "Responda agora com o que já tem, sem chamar ferramentas."})
    resp = await provider.chat(system, msgs, None)
    return resp.text or "…", trail
