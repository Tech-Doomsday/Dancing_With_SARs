"""OpenRouter backend: OpenRouter-specific units, live gateway, key check + model listing, probe,
and a REAL Claude Code session driven through the translator against a mock OpenRouter server.
(The Anthropic<->OpenAI translation itself is shared with Cloudflare and tested in test_cloudflare.py.)
Run: python tests/test_openrouter.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PORT = 18850
os.environ["OPENROUTER_API_BASE"] = f"http://127.0.0.1:{PORT}/api/v1"   # before importing devflock
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import aiohttp
from aiohttp import web

from devflock import openrouter as orr
from devflock.config import Endpoint, PROVIDER_OPENROUTER, OR_CLIENT_MODEL_ALIAS
from devflock.gateway import Gateway, Upstream
from devflock.probe import probe
from devflock.worker import Worker
import mock_openrouter_server as mor
from mock_openrouter_server import make_app, KEY

ok = True
ALIAS_ID = "~openai/gpt-sol-latest"        # the model id from the OpenRouter docs snippet
SONNET = "anthropic/claude-sonnet-4.5"
BASE = orr.api_base_url()


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra and not cond else ""))


def parse_sse(raw: bytes):
    events = []
    for block in raw.decode().split("\n\n"):
        if not block.strip():
            continue
        ev = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((ev.get("event"), json.loads(ev["data"])))
    return events


def rebuild(events):
    blocks, stop = {}, None
    for name, d in events:
        if name == "content_block_start":
            blocks[d["index"]] = dict(d["content_block"], _json="")
        elif name == "content_block_delta":
            b = blocks[d["index"]]
            if d["delta"]["type"] == "text_delta":
                b["text"] += d["delta"]["text"]
            elif d["delta"]["type"] == "input_json_delta":
                b["_json"] += d["delta"]["partial_json"]
        elif name == "message_delta":
            stop = d["delta"]["stop_reason"]
    out = []
    for i in sorted(blocks):
        b = blocks[i]
        if b["type"] == "tool_use":
            b["input"] = json.loads(b.pop("_json") or "{}")
        else:
            b.pop("_json")
        out.append(b)
    return out, stop


def ep_for(model=SONNET, key=KEY, **kw):
    return Endpoint("w", BASE, key, "bearer", model, PROVIDER_OPENROUTER, **kw)


# ---------------------------------------------------------------- 1. pure units
def unit_tests():
    models = orr.parse_models({"data": mor.MODELS})
    ids = [m.id for m in models]
    check("image-only models are dropped from the list", "google/gemini-image-gen" not in ids and len(ids) == 4, str(ids))
    son = next(m for m in models if m.id == SONNET)
    check("context, tools flag and price parsed (USD per 1M tokens)",
          son.context_window == 1000000 and son.tools is True and son.price == "$3/$15", son.price)
    cheap = next(m for m in models if m.id == "openai/gpt-oss-120b")
    check("sub-dollar prices keep their precision", cheap.price == "$0.05/$0.25", cheap.price)
    free = next(m for m in models if m.id.endswith(":free"))
    check("a :free model is shown as free", free.free and free.price == "free")
    nt = next(m for m in models if "llama-3.2" in m.id)
    check("no 'tools' in supported_parameters -> tools False", nt.tools is False)
    check("missing supported_parameters -> unknown (None)",
          orr.parse_models({"data": [{"id": "x/y"}]})[0].tools is None)

    models.sort(key=lambda m: (-m.created, m.id))
    check("default view: tool-capable only, newest first",
          [m.id for m in orr.default_view(models)] == [SONNET, "openai/gpt-oss-120b", "qwen/qwen3-coder:free"])
    check("search matches id or display name, case-insensitively",
          [m.id for m in orr.search(models, "CODER")] == ["qwen/qwen3-coder:free"]
          and [m.id for m in orr.search(models, "free")] == ["qwen/qwen3-coder:free"])
    check("looks_like_model_id: vendor/model, ~alias and :variant ok; plain words / spaces / leading / not",
          orr.looks_like_model_id(ALIAS_ID) and orr.looks_like_model_id("a/b:free")
          and not orr.looks_like_model_id("qwen") and not orr.looks_like_model_id("a b/c")
          and not orr.looks_like_model_id("/x"))

    h = orr.request_headers("k", "https://me.example", "My App")
    check("headers: Bearer key + HTTP-Referer + X-OpenRouter-Title",
          h["Authorization"] == "Bearer k" and h["HTTP-Referer"] == "https://me.example"
          and h["X-OpenRouter-Title"] == "My App" and h["Content-Type"] == "application/json")
    h = orr.request_headers("k")
    check("headers: attribution headers omitted when not set", "HTTP-Referer" not in h and "X-OpenRouter-Title" not in h)

    e = orr.describe_error(402, {"error": {"code": 402, "message": "Insufficient credits"}})
    check("402 -> out-of-credits hint + OpenRouter's own message", "out of credits" in e and "Insufficient credits" in e, e)
    e = orr.describe_error(502, {"error": {"message": "boom", "metadata": {"provider_name": "Acme"}}})
    check("provider name surfaced when OpenRouter gives one", "[provider: Acme]" in e, e)
    check("error text is safe on garbage input", orr.describe_error(500, "not a dict").startswith("HTTP 500"))
    check("body_error: HTTP-200 error object detected, status taken from its code",
          orr.body_error({"error": {"code": 502, "message": "x"}})[0] == 502
          and orr.body_error({"error": {"code": "weird", "message": "x"}})[0] == 502)
    check("body_error: a normal completion is not an error",
          orr.body_error({"choices": [{"message": {"content": "hi"}}]}) is None)

    os.environ.pop("OPENROUTER_MAX_TOKENS", None)
    check("max_tokens cap: unset -> None", orr.max_tokens_cap() is None)
    os.environ["OPENROUTER_MAX_TOKENS"] = "4096"
    check("max_tokens cap read from the environment", orr.max_tokens_cap() == 4096)
    os.environ["OPENROUTER_MAX_TOKENS"] = "lots"
    check("max_tokens cap: junk ignored", orr.max_tokens_cap() is None)
    os.environ.pop("OPENROUTER_MAX_TOKENS")

    t = orr.OrStreamTranslator(SONNET)
    t.feed(json.dumps({"choices": [{"delta": {"content": "par"}}]}))
    check("stream translator: no error on a clean stream", t.error is None)
    t.feed(json.dumps({"error": {"code": "server_error", "message": "Provider disconnected"},
                       "choices": [{"delta": {"content": ""}, "finish_reason": "error"}]}))
    check("stream translator: in-band error chunk recorded", t.error == "Provider disconnected")
    check("stream translator: still ends with a well-formed message", t.finish()[-1].startswith(b"event: message_stop"))

    d = ep_for()
    check("Endpoint: translated provider keeps the real key out of Claude Code",
          d.translated and d.client_key == "devflock-local" and d.client_model == OR_CLIENT_MODEL_ALIAS)
    check("Endpoint.masked() never shows the whole key", KEY not in d.masked() and "OpenRouter" in d.masked())
    old = {"name": "w", "base_url": "u", "api_key": "k", "auth_style": "bearer", "model_name": "m",
           "provider": "cloudflare", "gateway_id": ""}
    check("saved profiles from before this feature still load (new fields default)",
          Endpoint.from_dict(old).site_url == "" and Endpoint.from_dict(d.to_dict()) == d)


# ---------------------------------------------------------------- 2. live gateway
async def gateway_tests(app):
    ep = ep_for(ALIAS_ID, site_url="https://me.example", site_title="DevFlock Test")
    up = Upstream.from_endpoint(ep)
    check("Upstream.from_endpoint picks the openrouter backend",
          up.backend == "openrouter" and up.model == ALIAS_ID and up.site_title == "DevFlock Test")
    gw = Gateway("or1", up)
    port = await gw.start()
    u = f"http://127.0.0.1:{port}"
    msg = {"model": OR_CLIENT_MODEL_ALIAS, "max_tokens": 64, "messages": [{"role": "user", "content": "hello or"}]}
    async with aiohttp.ClientSession() as s:
        async with s.get(u + "/health") as r:
            d = await r.json()
            check("gateway /health reports the openrouter backend",
                  r.status == 200 and d["backend"] == "openrouter" and d["model"] == ALIAS_ID, str(d))

        async with s.post(u + "/v1/messages", json=msg, headers={"x-api-key": "devflock-local"}) as r:
            d = await r.json()
            check("non-stream: Anthropic-shaped reply", r.status == 200 and d["type"] == "message"
                  and d["content"][0]["text"] == "Echo: hello or" and d["usage"]["input_tokens"] == 11, str(d))
            check("OpenRouter's reasoning field is not shown as answer text",
                  all("thinking" not in (b.get("text") or "") for b in d["content"]))
        sent, hdr = app["requests"][-1], app["headers"][-1]
        check("upstream got the REAL model id (incl. ~latest alias), not Claude Code's alias", sent["model"] == ALIAS_ID)
        check("upstream got Authorization: Bearer <openrouter key> (client key not forwarded)",
              hdr.get("Authorization") == f"Bearer {KEY}")
        check("upstream got HTTP-Referer and X-OpenRouter-Title",
              hdr.get("HTTP-Referer") == "https://me.example" and hdr.get("X-OpenRouter-Title") == "DevFlock Test", str(hdr))

        async with s.post(u + "/v1/messages", json={**msg, "stream": True}) as r:
            raw = await r.read()
        blocks, stop = rebuild(parse_sse(raw))
        names = [e for e, _ in parse_sse(raw)]
        check("stream text (with keep-alive comment + reasoning chunks): full envelope, clean text",
              names[0] == "message_start" and names[-1] == "message_stop"
              and blocks[0]["text"] == "Echo: hello or" and stop == "end_turn", str(names))

        tool_msg = {**msg, "stream": True, "tools": [{"name": "get_time", "description": "t",
                                                     "input_schema": {"type": "object", "properties": {}}}],
                    "messages": [{"role": "user", "content": "please call get_time"}]}
        async with s.post(u + "/v1/messages", json=tool_msg) as r:
            blocks, stop = rebuild(parse_sse(await r.read()))
        check("stream tool call: tool_use block + stop_reason tool_use",
              blocks and blocks[-1]["type"] == "tool_use" and blocks[-1]["name"] == "get_time" and stop == "tool_use", str(blocks))

        async with s.post(u + "/v1/messages/count_tokens", json=msg) as r:
            check("count_tokens answered locally", (await r.json())["input_tokens"] >= 1)
        async with s.post(u + "/v1/foo", json={}) as r:
            d = await r.json()
            check("unknown route -> Anthropic-style 404 naming the backend",
                  r.status == 404 and d["type"] == "error" and "openrouter" in d["error"]["message"])

        # max_tokens cap
        os.environ["OPENROUTER_MAX_TOKENS"] = "50"
        async with s.post(u + "/v1/messages", json={**msg, "max_tokens": 64000}) as r:
            await r.read()
        os.environ.pop("OPENROUTER_MAX_TOKENS")
        check("OPENROUTER_MAX_TOKENS lowers an oversized max_tokens before it reaches OpenRouter",
              app["requests"][-1]["max_tokens"] == 50)
        async with s.post(u + "/v1/messages", json={**msg, "max_tokens": 64000}) as r:
            await r.read()
        check("... and without it max_tokens is passed through untouched", app["requests"][-1]["max_tokens"] == 64000)
    await gw.stop()

    async def serve(port_off, **kw):
        a = make_app(**kw)
        run = web.AppRunner(a); await run.setup(); await web.TCPSite(run, "127.0.0.1", PORT + port_off).start()
        return a, run

    def gw_for(name, port_off, key=KEY):
        return Gateway(name, Upstream(f"http://127.0.0.1:{PORT + port_off}/api/v1", key, "bearer", "openrouter", SONNET))

    # 429 x2 then success: retried before any byte reaches the client
    import devflock.gateway as gwm
    saved = gwm.RETRY_BACKOFFS; gwm.RETRY_BACKOFFS = [0.05, 0.05, 0.05]
    _, run2 = await serve(1, fail_first=2, fail_status=429)
    gw2 = gw_for("or2", 1); p2 = await gw2.start()
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{p2}/v1/messages", json=msg) as r:
            check("429 x2 then OK: retried transparently", r.status == 200 and gw2.stats.retries == 2,
                  f"status={r.status} retries={gw2.stats.retries}")
    await gw2.stop(); await run2.cleanup()

    # 402 out of credits: NOT retried, surfaced with an actionable hint
    a3, run3 = await serve(2, credits_402=True)
    gw3 = gw_for("or3", 2); p3 = await gw3.start()
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{p3}/v1/messages", json=msg) as r:
            d = await r.json()
            check("402: surfaced as an Anthropic error naming OpenRouter + the credits hint",
                  r.status == 402 and "OpenRouter" in d["error"]["message"] and "out of credits" in d["error"]["message"], str(d))
            check("402 is not retried (it would just burn time)", gw3.stats.retries == 0)
    await gw3.stop(); await run3.cleanup(); gwm.RETRY_BACKOFFS = saved

    # bad key -> 401 with a useful hint, key never echoed
    bad = gw_for("or4", 0, key="wrong-key"); pb = await bad.start()
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{pb}/v1/messages", json=msg) as r:
            d = await r.json()
            check("bad key: 401 + authentication_error + key hint",
                  r.status == 401 and d["error"]["type"] == "authentication_error" and "API key" in d["error"]["message"], str(d))
            check("key never appears in the error text", "wrong-key" not in json.dumps(d))
    await bad.stop()

    # HTTP 200 carrying an error object (no choices)
    _, run5 = await serve(3, body_error=True)
    gw5 = gw_for("or5", 3); p5 = await gw5.start()
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{p5}/v1/messages", json=msg) as r:
            d = await r.json()
            check("HTTP 200 + error body -> real error status, not an empty 'successful' message",
                  r.status == 502 and d["type"] == "error", f"{r.status} {d}")
    await gw5.stop(); await run5.cleanup()

    # in-band mid-stream error
    _, run6 = await serve(4, mid_stream_error=True)
    gw6 = gw_for("or6", 4); p6 = await gw6.start()
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://127.0.0.1:{p6}/v1/messages", json={**msg, "stream": True}) as r:
            events = parse_sse(await r.read())
    names = [e for e, _ in events]
    check("mid-stream provider error reaches Claude Code as an Anthropic `error` event",
          names[-1] == "error" and "Provider disconnected" in events[-1][1]["error"]["message"], str(names))
    check("... and the truncated message is NOT closed as if it had succeeded", "message_stop" not in names)
    await gw6.stop(); await run6.cleanup()


# ---------------------------------------------------------------- 3. key check, models, probe
async def listing_and_probe():
    creds = orr.OrCreds(KEY)
    info = await orr.check_key(creds)
    check("check_key: valid key accepted, label + free-tier flag parsed",
          info.label.startswith("sk-or-") and info.is_free_tier is False, str(info))
    try:
        await orr.check_key(orr.OrCreds("nope"))
        check("check_key: bad key raises", False)
    except RuntimeError as e:
        check("check_key: bad key raises a readable error without echoing the key",
              "401" in str(e) and "nope" not in str(e), str(e))

    models = await orr.list_text_models(creds)
    check("list_text_models: newest first, image-only model filtered",
          [m.id for m in models] == [SONNET, "openai/gpt-oss-120b", "qwen/qwen3-coder:free", "meta-llama/llama-3.2-1b-instruct"],
          str([m.id for m in models]))

    r = await probe(ep_for())
    check("probe: openrouter model ok, bearer auth", r.ok and r.auth_style == "bearer", r.error or "")
    check("probe: tool call smoke test passes", r.tools_ok is True)
    r = await probe(ep_for(key="bad"))
    check("probe: bad key reported with a key hint", (not r.ok) and "API key" in (r.error or ""), r.error or "")
    check("probe error never contains the key", "bad" not in (r.error or "").replace("bad key", ""))
    r = await probe(ep_for("meta-llama/llama-3.2-1b-instruct"))
    check("probe: model that cannot call tools -> reply ok but tools ✗ + warning",
          r.ok and r.tools_ok is False and any("tool call" in w for w in r.warnings), str(r))
    r = await probe(ep_for(ALIAS_ID, site_url="https://me.example"))
    check("probe: a pasted ~latest alias id is just sent through", r.ok)


# ---------------------------------------------------------------- 4. REAL Claude Code through the translator
async def real_claude_code(app):
    ep = ep_for(SONNET, site_title="DevFlock Test")
    gw = Gateway("or-real", Upstream.from_endpoint(ep))
    port = await gw.start()
    region = Path(tempfile.mkdtemp(prefix="devflock-or-"))
    w = Worker("worker-1", port, ep.client_key, region, model_name=ep.client_model)
    check("worker env gets a placeholder key, not the OpenRouter key",
          w._base_env()["ANTHROPIC_API_KEY"] == "devflock-local" and KEY not in json.dumps(w._base_env()))
    target = region / "or_hello.txt"
    res = await w.run_task(f"Use the Write tool to create a file at the absolute path {target} containing a greeting.",
                           max_turns=4, allowed_tools=["Write"], resume=False)
    check("Claude Code completed the task via the OpenRouter mock", res.ok, f"error={res.error} summary={res.summary!r}")
    check("tool call made and file written", "Write" in res.tool_calls and target.exists()
          and "openrouter mock" in target.read_text(), f"calls={res.tool_calls}")
    check("final answer came back after the tool result", "Done." in res.summary, res.summary)
    reqs = app["requests"]
    check("tool results were sent upstream as role=tool messages",
          any(m["role"] == "tool" for r in reqs for m in r["messages"]))
    check("every upstream request used the real OpenRouter model id", all(r["model"] == SONNET for r in reqs))
    check("every upstream request carried the attribution header",
          all(h.get("X-OpenRouter-Title") == "DevFlock Test" for h in app["headers"]))
    await gw.stop()


async def main():
    unit_tests()
    app = make_app()
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    await gateway_tests(app)
    await listing_and_probe()
    app["requests"].clear(); app["headers"].clear()
    await real_claude_code(app)
    await runner.cleanup()
    print("\nALL OPENROUTER TESTS " + ("PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


asyncio.run(main())
