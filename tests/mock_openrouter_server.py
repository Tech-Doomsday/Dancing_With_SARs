"""A stand-in for OpenRouter's API.

Serves, under /api/v1/ :
    GET  auth/key              (Bearer auth; key label + is_free_tier)
    GET  models                (public catalogue: context_length, supported_parameters, pricing,
                                architecture.output_modalities, created)
    POST chat/completions      (Bearer auth; streaming and non-streaming; tool calls)

Scripted behaviour (so a REAL Claude Code binary can be driven through the gateway) is the same as
mock_cloudflare_server.py: a Write tool call when the prompt names an absolute path, or for
DevFlock's real worker prompt a Write of hello.py into the first writable folder; otherwise an echo.

OpenRouter-specific behaviour that can be switched on:
    fail_first / fail_status   N upstream failures before succeeding (retry testing)
    credits_402                every chat answers OpenRouter's "insufficient credits" 402
    body_error                 HTTP 200 whose body is an {"error": ...} object, no choices
    mid_stream_error           the stream dies with OpenRouter's in-band error chunk
    no_tool_models             models that answer 404 "No endpoints found ... tool use" when tools are sent
Every accepted chat body and its request headers are kept in app["requests"] / app["headers"].

Not part of the shipped package.
"""
from __future__ import annotations

import json
import re

from aiohttp import web

KEY = "sk-or-test-key"

MODELS = [
    {"id": "anthropic/claude-sonnet-4.5", "name": "Anthropic: Claude Sonnet 4.5", "created": 1760000000,
     "context_length": 1000000, "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
     "supported_parameters": ["tools", "tool_choice", "max_tokens", "temperature"]},
    {"id": "openai/gpt-oss-120b", "name": "OpenAI: gpt-oss-120b", "created": 1754000000,
     "context_length": 131072, "pricing": {"prompt": "0.00000005", "completion": "0.00000025"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["tools", "tool_choice", "max_tokens"]},
    {"id": "qwen/qwen3-coder:free", "name": "Qwen: Qwen3 Coder (free)", "created": 1753000000,
     "context_length": 262144, "pricing": {"prompt": "0", "completion": "0"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["tools", "tool_choice", "max_tokens"]},
    {"id": "meta-llama/llama-3.2-1b-instruct", "name": "Meta: Llama 3.2 1B Instruct", "created": 1727000000,
     "context_length": 60000, "pricing": {"prompt": "0.000000005", "completion": "0.00000001"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["max_tokens", "temperature"]},
    {"id": "google/gemini-image-gen", "name": "Google: Image generator", "created": 1761000000,
     "context_length": 32768, "pricing": {"prompt": "0.000001", "completion": "0.00003"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
     "supported_parameters": ["max_tokens"]},
]
NO_TOOL_MODELS = frozenset({"meta-llama/llama-3.2-1b-instruct"})


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def make_app(key: str = KEY, fail_first: int = 0, fail_status: int = 429, credits_402: bool = False,
             body_error: bool = False, mid_stream_error: bool = False, free_tier: bool = False,
             no_tool_models: frozenset = NO_TOOL_MODELS) -> web.Application:
    app = web.Application()
    app["requests"] = []
    app["headers"] = []
    st = {"fails_left": fail_first}

    def auth_ok(request):
        return request.headers.get("Authorization") == f"Bearer {key}"

    def err(status: int, message: str, **extra):
        return web.json_response({"error": {"code": status, "message": message, **extra}}, status=status)

    async def auth_key(request):
        if not auth_ok(request):
            return err(401, "No auth credentials found")
        return web.json_response({"data": {"label": "sk-or-v1-abc...xyz", "limit": None, "usage": 0.5,
                                           "is_free_tier": free_tier, "limit_remaining": None}})

    async def models(request):
        return web.json_response({"data": MODELS})        # public: no auth needed, like the real one

    async def chat(request):
        if not auth_ok(request):
            return err(401, "No auth credentials found")
        body = await request.json()
        if credits_402:
            return err(402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits")
        if st["fails_left"] > 0:
            st["fails_left"] -= 1
            return err(fail_status, "Provider returned error", metadata={"provider_name": "MockProvider"})
        if body.get("tools") and body.get("model") in no_tool_models:
            return err(404, f"No endpoints found that support tool use for {body.get('model')}")
        app["requests"].append(body)
        app["headers"].append(dict(request.headers))
        if body_error:
            return web.json_response({"error": {"code": 502, "message": "Provider returned error"},
                                      "user_id": "u1"})
        msgs = body.get("messages", [])
        tools = {t["function"]["name"] for t in body.get("tools") or []}

        first_user = next((_text(m["content"]) for m in msgs if m["role"] == "user"), "")
        last = msgs[-1] if msgs else {}
        m = re.search(r"absolute path (\S+)", first_user)
        tool_call = None
        if last.get("role") == "tool":
            reply = "Done."
        elif "Write" in tools and m:
            path = m.group(1).rstrip(".,)`'\"")
            tool_call = ("Write", {"file_path": path, "content": "hi from openrouter mock\n"})
            reply = None
        elif "Write" in tools and re.search(r"inside these folders[^\n]*\n\s*- (\S+)", first_user):
            folder = re.search(r"inside these folders[^\n]*\n\s*- (\S+)", first_user).group(1)
            tool_call = ("Write", {"file_path": f"{folder}/hello.py", "content": "print('hi from openrouter')\n"})
            reply = None
        elif "get_time" in tools and "get_time" in first_user:
            tool_call = ("get_time", {})
            reply = None
        else:
            reply = "Echo: " + (_text(last.get("content")) if last.get("role") == "user" else first_user)[:200]

        if not body.get("stream"):
            msg = {"role": "assistant", "content": reply}
            if tool_call:
                msg["content"] = None
                msg["tool_calls"] = [{"id": "call_1", "type": "function",
                                      "function": {"name": tool_call[0], "arguments": json.dumps(tool_call[1])}}]
            msg["reasoning"] = "thinking..."      # OpenRouter's reasoning field; must never be shown
            return web.json_response({
                "id": "gen-1", "object": "chat.completion", "provider": "MockProvider", "model": body["model"],
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_call else "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})

        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)

        async def send(obj):
            await resp.write(b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n")

        def chunk(delta, finish=None):
            return {"id": "gen-1", "object": "chat.completion.chunk", "provider": "MockProvider",
                    "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        await resp.write(b": OPENROUTER PROCESSING\n\n")          # keep-alive comment, must be ignored
        await send(chunk({"role": "assistant", "content": ""}))
        await send(chunk({"reasoning": "hmm"}))
        if mid_stream_error:
            await send(chunk({"content": "Echo: par"}))
            await send({"id": "gen-1", "provider": "MockProvider", "model": body["model"],
                        "error": {"code": "server_error", "message": "Provider disconnected unexpectedly"},
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                     "finish_reason": "error"}]})
            await resp.write_eof()
            return resp
        if tool_call:
            args = json.dumps(tool_call[1])
            third = max(1, len(args) // 3)
            await send(chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                              "function": {"name": tool_call[0], "arguments": ""}}]}))
            for i in range(0, len(args), third):
                await send(chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + third]}}]}))
            await send(chunk({}, "tool_calls"))
        else:
            for i in range(0, len(reply), 6):
                await send(chunk({"content": reply[i:i + 6]}))
            await send(chunk({}, "stop"))
        await send({"id": "gen-1", "object": "chat.completion.chunk", "choices": [],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
        await send(b"[DONE]")
        await resp.write_eof()
        return resp

    app.router.add_get("/api/v1/auth/key", auth_key)
    app.router.add_get("/api/v1/models", models)
    app.router.add_post("/api/v1/chat/completions", chat)
    return app
