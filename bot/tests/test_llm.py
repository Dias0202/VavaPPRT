"""Provedores de LLM (formato de fio), fallback e loop de tools."""
import json

import httpx
import pytest

from x9.llm import (FallbackProvider, GeminiProvider, GroqProvider, LLMError, LLMResponse,
                    ProviderHealth, gemini_contents, gemini_tools, openai_messages,
                    run_tools_loop)

CATALOG = [
    {"name": "ranking_grupo", "description": "ranking", "parameters":
        {"type": "object", "properties": {}, "required": []}},
    {"name": "narrar_round", "description": "narra", "parameters":
        {"type": "object", "properties": {"round": {"type": "integer", "description": "n"},
                                          "evento": {"type": "string", "enum": ["a", "b"]}},
         "required": ["round"]}},
]

HISTORY = [
    {"role": "user", "content": "conta o round 3"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "name": "narrar_round", "args": {"round": 3}},
                    {"id": "c2", "name": "ranking_grupo", "args": {}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "narrar_round", "content": {"round": 3}},
    {"role": "tool", "tool_call_id": "c2", "name": "ranking_grupo", "content": {"ranking": []}},
]


def test_schema_do_gemini():
    decls = gemini_tools(CATALOG)[0]["functionDeclarations"]
    assert "parameters" not in decls[0]  # OBJECT vazio é rejeitado pelo Gemini
    p = decls[1]["parameters"]
    assert p["type"] == "OBJECT" and p["properties"]["round"]["type"] == "INTEGER"
    assert p["properties"]["evento"]["enum"] == ["a", "b"] and p["required"] == ["round"]
    assert gemini_tools([]) == []


def test_historico_no_formato_do_gemini():
    c = gemini_contents(HISTORY)
    assert [x["role"] for x in c] == ["user", "model", "user"]
    assert [p["functionCall"]["name"] for p in c[1]["parts"]] == ["narrar_round", "ranking_grupo"]
    # as duas respostas de tool vão juntas no mesmo turno
    assert [p["functionResponse"]["name"] for p in c[2]["parts"]] == ["narrar_round", "ranking_grupo"]
    assert c[2]["parts"][0]["functionResponse"]["response"] == {"result": {"round": 3}}


def test_gemini_ecoa_as_parts_originais():
    raw = [{"functionCall": {"name": "x", "args": {}}, "thoughtSignature": "sig"}]
    c = gemini_contents([{"role": "assistant", "content": None, "tool_calls": [], "raw": {"gemini": raw}}])
    assert c[0]["parts"] == raw


def test_tool_result_gigante_e_cortado():
    big = {"x": "a" * 20000}
    c = gemini_contents([{"role": "tool", "tool_call_id": "c", "name": "t", "content": big}])
    res = c[0]["parts"][0]["functionResponse"]["response"]["result"]
    assert isinstance(res, str) and res.endswith("(cortado)")


def test_historico_no_formato_openai():
    m = openai_messages("sys", HISTORY)
    assert m[0] == {"role": "system", "content": "sys"}
    assert m[2]["tool_calls"][0]["function"]["arguments"] == '{"round": 3}'
    assert m[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"round": 3}'}


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_gemini_le_function_call_e_texto():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers.get("x-goog-api-key")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "pensando", "thought": True},
            {"text": "Vou ver."},
            {"functionCall": {"name": "narrar_round", "args": {"round": 13}}}]},
            "finishReason": "STOP"}]})

    p = GeminiProvider("k", "gemini-x", client=_client(handler))
    r = await p.chat("persona", [{"role": "user", "content": "oi"}], CATALOG)
    assert r.text == "Vou ver."  # pensamento não vaza
    assert r.tool_calls[0]["name"] == "narrar_round" and r.tool_calls[0]["args"] == {"round": 13}
    assert seen["key"] == "k"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "persona"
    assert {s["category"]: s["threshold"] for s in seen["body"]["safetySettings"]}[
        "HARM_CATEGORY_HARASSMENT"] == "BLOCK_NONE"


@pytest.mark.parametrize("status,body", [
    (429, {"error": "quota"}),
    (503, {}),
    (200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}),
    (200, {"candidates": [{"finishReason": "SAFETY"}]}),
])
async def test_gemini_falhas_viram_llmerror(status, body):
    p = GeminiProvider("k", "m", client=_client(lambda req: httpx.Response(status, json=body)))
    with pytest.raises(LLMError):
        await p.chat("s", [{"role": "user", "content": "x"}])


async def test_groq_le_tool_calls():
    def handler(req):
        body = json.loads(req.content)
        assert body["model"] == "llama" and body["tools"][1]["function"]["name"] == "narrar_round"
        assert req.headers["authorization"] == "Bearer g"
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "narrar_round", "arguments": '{"round": 2}'}}]}}]})

    p = GroqProvider("g", "llama", client=_client(handler))
    r = await p.chat("s", [{"role": "user", "content": "x"}], CATALOG)
    assert r.tool_calls == [{"id": "t1", "name": "narrar_round", "args": {"round": 2}}]


class Scripted:
    def __init__(self, name, replies):
        self.name, self.replies, self.calls = name, list(replies), []

    async def chat(self, system, messages, tools=None, temperature=0.9):
        self.calls.append((system, list(messages), tools))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def test_fallback_troca_de_provedor_e_poe_de_castigo():
    t = [0.0]
    g = Scripted("gemini", [LLMError("429"), LLMResponse(text="de volta", provider="gemini")])
    q = Scripted("groq", [LLMResponse(text="groq aqui", provider="groq"),
                          LLMResponse(text="groq de novo", provider="groq")])
    fb = FallbackProvider([g, q], cooldown_s=60, clock=lambda: t[0])
    assert (await fb.chat("s", [])).text == "groq aqui"
    assert (await fb.chat("s", [])).text == "groq de novo"  # gemini de castigo
    assert len(g.calls) == 1
    t[0] = 61
    assert (await fb.chat("s", [])).text == "de volta"


async def test_fallback_todos_falham():
    fb = FallbackProvider([Scripted("a", [LLMError("x")]), Scripted("b", [LLMError("y")])])
    with pytest.raises(LLMError, match="a: x | b: y"):
        await fb.chat("s", [])
    with pytest.raises(ValueError):
        FallbackProvider([])


async def test_loop_executa_tools_e_devolve_texto():
    p = Scripted("gemini", [
        LLMResponse(tool_calls=[{"id": "1", "name": "narrar_round", "args": {"round": 1}},
                                {"id": "2", "name": "ranking_grupo", "args": {}}],
                    raw={"gemini": ["parts"]}, provider="gemini"),
        LLMResponse(text="O admin carregou.", provider="gemini"),
    ])
    calls = []

    async def executor(name, args):
        calls.append((name, args))
        return {"ok": name}

    text, trail = await run_tools_loop(p, "s", [{"role": "user", "content": "q"}], CATALOG, executor)
    assert text == "O admin carregou."
    assert calls == [("narrar_round", {"round": 1}), ("ranking_grupo", {})]
    assert [t[0] for t in trail] == ["narrar_round", "ranking_grupo"]
    second = p.calls[1][1]
    assert second[1]["raw"] == {"gemini": ["parts"]}
    assert [m["role"] for m in second] == ["user", "assistant", "tool", "tool"]


async def test_loop_forca_resposta_quando_o_modelo_nao_para():
    call = LLMResponse(tool_calls=[{"id": "1", "name": "ranking_grupo", "args": {}}])
    p = Scripted("x", [call, call, LLMResponse(text="chega")])

    async def executor(name, args):
        return {}

    text, trail = await run_tools_loop(p, "s", [], CATALOG, executor, max_steps=2)
    assert text == "chega" and len(trail) == 2
    assert p.calls[-1][2] is None  # última chamada sem tools


async def test_429_curto_espera_e_tenta_de_novo():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message":
                "Rate limit reached on tokens per minute (TPM). Please try again in 8.205s."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "voltei"}}]})

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    groq = GroqProvider("g", "m", client=_client(handler))
    fb = FallbackProvider([groq], sleep=fake_sleep)
    r = await fb.chat("s", [{"role": "user", "content": "x"}])
    assert r.text == "voltei" and len(calls) == 2
    assert slept == [pytest.approx(8.705)]


async def test_429_longo_nao_espera():
    gem = GeminiProvider("k", "m", client=_client(lambda req: httpx.Response(
        429, json={"error": {"details": [{"retryDelay": "3600s"}]}})))
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    with pytest.raises(LLMError) as e:
        await FallbackProvider([gem], sleep=fake_sleep).chat("s", [])
    assert slept == [] and "429" in str(e.value)


def test_le_retry_after_de_cada_formato():
    from x9.llm import _retry_after
    assert _retry_after(httpx.Response(429, headers={"retry-after": "7"})) == 7
    assert _retry_after(httpx.Response(429, text='"retryDelay": "12s"')) == 12
    assert _retry_after(httpx.Response(429, text="Please try again in 1.5s.")) == 1.5
    assert _retry_after(httpx.Response(429, text="cota diária")) is None


def test_retry_after_em_milissegundos():
    from x9.llm import _retry_after
    assert _retry_after(httpx.Response(429, text="try again in 899.99ms. Need")) == pytest.approx(0.89999)


async def test_espera_curta_varias_vezes_dentro_do_orcamento():
    n = []

    def handler(req):
        n.append(1)
        if len(n) < 3:
            return httpx.Response(429, text="Please try again in 5s.")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    slept = []

    async def fake_sleep(s):
        slept.append(s)

    fb = FallbackProvider([GroqProvider("g", "m", client=_client(handler))], sleep=fake_sleep)
    assert (await fb.chat("s", [])).text == "ok"
    assert slept == [5.5, 5.5]


def test_opcionais_aceitam_null_no_groq():
    from x9.llm import openai_tools
    params = openai_tools(CATALOG)[1]["function"]["parameters"]
    assert params["properties"]["round"]["type"] == "integer"          # obrigatório
    assert params["properties"]["evento"]["type"] == ["string", "null"]  # opcional
    assert params["properties"]["evento"]["enum"] == ["a", "b", None]
    assert CATALOG[1]["parameters"]["properties"]["evento"]["type"] == "string"  # não mutou


async def test_tool_use_failed_tenta_de_novo():
    n = []

    def handler(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(400, json={"error": {"code": "tool_use_failed"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def no_sleep(s):
        pass

    fb = FallbackProvider([GroqProvider("g", "m", client=_client(handler))], sleep=no_sleep)
    assert (await fb.chat("s", [])).text == "ok" and len(n) == 2


async def test_sem_raciocinio_lento():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        if "googleapis" in str(req.url):
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "oi"}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "oi"}}]})

    await GeminiProvider("k", "gemini-2.5-flash", client=_client(handler)).chat("s", [])
    await GroqProvider("g", "openai/gpt-oss-120b", client=_client(handler)).chat("s", [])
    assert seen[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert seen[1]["reasoning_effort"] == "low"


async def test_castigo_dobra_enquanto_continua_falhando():
    t = [0.0]
    g = Scripted("gemini", [LLMError("cota"), LLMError("cota"), LLMResponse(text="voltei")])
    q = Scripted("groq", [LLMResponse(text="q")] * 5)
    fb = FallbackProvider([g, q], cooldown_s=60, clock=lambda: t[0])
    await fb.chat("s", [])           # gemini falha: castigo de 60 s
    t[0] = 61
    await fb.chat("s", [])           # falha de novo: castigo de 120 s
    t[0] = 61 + 100
    await fb.chat("s", [])
    assert len(g.calls) == 2         # ainda de castigo
    t[0] = 61 + 121
    assert (await fb.chat("s", [])).text == "voltei"


# ---------------------------------------------------------------------------
# Streaming (X9 v2)
# ---------------------------------------------------------------------------
def _sse_body(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


async def _collect(gen):
    texts, done = [], None
    async for kind, val in gen:
        if kind == "text":
            texts.append(val)
        else:
            done = val
    return texts, done


async def test_gemini_em_streaming_junta_texto_e_function_call():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, text=_sse_body([
            {"candidates": [{"content": {"parts": [{"text": "pensei", "thought": True}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "O admin "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "carregou."}]}}]},
            {"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "ranking_grupo", "args": {}}, "thoughtSignature": "s"}]},
                "finishReason": "STOP"}]},
        ]), headers={"content-type": "text/event-stream"})

    p = GeminiProvider("k", "gemini-x", client=_client(handler))
    texts, done = await _collect(p.chat_stream("s", [{"role": "user", "content": "x"}], CATALOG))
    assert "streamGenerateContent" in seen["url"] and "alt=sse" in seen["url"]
    assert texts == ["O admin ", "carregou."] and done.text == "O admin carregou."
    assert done.tool_calls[0]["name"] == "ranking_grupo"
    # partes ecoáveis: texto emendado num só, function call com a assinatura
    assert done.raw["gemini"][0] == {"text": "O admin carregou."}
    assert done.raw["gemini"][1]["thoughtSignature"] == "s"


async def test_groq_em_streaming_monta_tool_call_em_pedacos():
    def handler(req):
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200, text=_sse_body([
            {"choices": [{"delta": {"content": "Fala "}}]},
            {"choices": [{"delta": {"content": "aí."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function":
                                                    {"name": "narrar_", "arguments": '{"rou'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function":
                                                    {"name": "round", "arguments": 'nd": 4}'}}]}}]},
        ]))

    p = GroqProvider("g", "m", client=_client(handler))
    texts, done = await _collect(p.chat_stream("s", [{"role": "user", "content": "x"}], CATALOG))
    assert "".join(texts) == "Fala aí."
    assert done.tool_calls == [{"id": "t1", "name": "narrar_round", "args": {"round": 4}}]


async def test_streaming_429_vira_llmerror_com_espera():
    p = GroqProvider("g", "m", client=_client(lambda req: httpx.Response(
        429, text="Please try again in 2.5s.")))
    with pytest.raises(LLMError) as e:
        await _collect(p.chat_stream("s", []))
    assert e.value.retry_after == 2.5


async def test_fallback_em_streaming_troca_antes_do_primeiro_texto():
    bad = GroqProvider("g", "m", client=_client(lambda req: httpx.Response(500, text="x")),
                       name="ruim")
    def good_handler(req):
        if json.loads(req.content).get("stream"):
            return httpx.Response(200, text=_sse_body([{"choices": [{"delta": {"content": "ok"}}]}]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    good = GroqProvider("g", "m", client=_client(good_handler), name="bom")
    health = ProviderHealth()
    fb = FallbackProvider([bad, good], health=health)
    texts, done = await _collect(fb.chat_stream("s", []))
    assert texts == ["ok"] and done.provider == "bom"
    # o castigo vale para outra rota que compartilha a saúde
    other = FallbackProvider([bad, good], health=health)
    assert not health.ready(bad) and (await other.chat("s", [])).provider == "bom"
    st = {x["provedor"]: x for x in fb.status()}
    assert st["ruim"]["ok"] is False and st["bom"]["ok"] is True


async def test_aviso_de_espera_da_cota():
    from x9.llm import WAIT_NOTICE
    n = []

    def handler(req):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(429, text="Please try again in 3s.")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    avisos = []

    async def notice(s):
        avisos.append(s)

    async def no_sleep(s):
        pass

    token = WAIT_NOTICE.set(notice)
    try:
        fb = FallbackProvider([GroqProvider("g", "m", client=_client(handler))], sleep=no_sleep)
        assert (await fb.chat("s", [])).text == "ok"
    finally:
        WAIT_NOTICE.reset(token)
    assert avisos == [3.0]


async def test_loop_com_streaming_e_cronometro():
    class Streamer:
        name = "s"

        def __init__(self):
            self.n = 0

        async def chat_stream(self, system, messages, tools=None, temperature=0.9):
            self.n += 1
            if self.n == 1:
                yield ("text", "Vou ver.")
                yield ("done", LLMResponse(text="Vou ver.", provider="s", tool_calls=[
                    {"id": "1", "name": "ranking_grupo", "args": {}}]))
            else:
                yield ("text", "Admin ")
                yield ("text", "lidera.")
                yield ("done", LLMResponse(text="Admin lidera.", provider="s"))

    got = []

    async def on_text(t, step):
        got.append((step, t))

    async def executor(name, args):
        return {"ok": 1}

    stats = {}
    text, trail = await run_tools_loop(Streamer(), "s", [], CATALOG, executor, on_text=on_text,
                                       stats=stats)
    assert text == "Admin lidera." and [t[0] for t in trail] == ["ranking_grupo"]
    assert got == [(0, "Vou ver."), (1, "Admin "), (1, "lidera.")]
    assert stats["steps"] == 2 and stats["tools"] == ["ranking_grupo"] and stats["provider"] == "s"
