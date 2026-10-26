"""Mono mode (`devflock --mono` / `devflock-mono`): one model, one ordinary Claude Code session.

Part A needs only the standard library plus the real `claude` binary. A stdlib mock Anthropic server stands
in for DevFlock's gateway and records what Claude Code actually sends. Checks, against a HOSTILE home/project
(settings files that redirect ANTHROPIC_BASE_URL and force another model, and an ANTHROPIC_API_KEY in the
parent environment):
   * the session still goes to our port, with our model alias and the placeholder token, never the real key
   * the real key is not in the child's environment or in the settings file handed to Claude Code
   * the interactive UI starts without any login/API-key prompt and a typed prompt reaches the endpoint
Part B (needs aiohttp, i.e. a normal DevFlock install): the REAL Gateway in front of tests/mock_llama_server.py:
   Claude Code holds only the placeholder, the gateway injects the real key, the mock accepts it, exit code 0.

Run: python tests/test_mono.py
"""
import asyncio, json, os, pty, re, select, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A clean "normal machine": no host-managed Claude/Anthropic variables leaking into the child.
for _k in list(os.environ):
    if _k.startswith(("CLAUDE", "ANTHROPIC", "CCR_")) or _k == "IS_SANDBOX":
        del os.environ[_k]
os.environ["ANTHROPIC_API_KEY"] = "parent-env-key"          # must be stripped from the child
os.environ["DEVFLOCK_CONFIG_DIR"] = __import__("tempfile").mkdtemp(prefix="mono-cfg-")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from devflock import mono
from devflock.config import Endpoint, PROVIDER_OPENROUTER, PROVIDER_ANTHROPIC

ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra and not cond else ""))


# ---- stdlib mock of the Anthropic Messages API -----------------------------------------------------
LOG: list[dict] = []
def _sse(ev, data): return f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode()

class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        LOG.append({"path": self.path, "method": "GET"}); self.send_response(404); self.end_headers()
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        try: j = json.loads(body)
        except Exception: j = {}
        LOG.append({"path": self.path, "method": "POST", "model": j.get("model"),
                    "auth": self.headers.get("authorization"), "x-api-key": self.headers.get("x-api-key")})
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
            self.wfile.write(b'{"input_tokens": 10}'); return
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        w = self.wfile.write
        w(_sse("message_start", {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant", "model": j.get("model"), "content": [], "stop_reason": None, "usage": {"input_tokens": 5, "output_tokens": 0}}}))
        w(_sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
        w(_sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "pong"}}))
        w(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        w(_sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}))
        w(_sse("message_stop", {"type": "message_stop"}))

def posts():
    return [l for l in LOG if l["method"] == "POST" and l["path"].startswith("/v1/messages")]


def hostile_home_and_project():
    home, proj = tempfile.mkdtemp(prefix="mono-home-"), tempfile.mkdtemp(prefix="mono-proj-")
    os.makedirs(f"{home}/.claude"); os.makedirs(f"{proj}/.claude")
    json.dump({"model": "opus", "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_MODEL": "bogus",
                                         "ANTHROPIC_AUTH_TOKEN": "user-token"}}, open(f"{home}/.claude/settings.json", "w"))
    json.dump({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}, open(f"{proj}/.claude/settings.json", "w"))
    return home, proj


def part_a():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    claude = mono.find_claude()
    check("find_claude() locates a Claude Code binary", bool(claude))
    ep = Endpoint(name="mono", base_url="https://openrouter.ai/api/v1", api_key="REAL-SECRET-KEY", auth_style="bearer",
                  model_name="vendor/real-model", provider=PROVIDER_OPENROUTER, context_window=64_000)

    env = mono.session_env(ep, port)
    check("child env: base url is the local gateway", env["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{port}")
    check("child env: placeholder token, no ANTHROPIC_API_KEY", env["ANTHROPIC_AUTH_TOKEN"] == "devflock-local"
          and "ANTHROPIC_API_KEY" not in env)
    check("child env: real key absent", "REAL-SECRET-KEY" not in json.dumps(env))
    check("child env: all model aliases and subagents use the alias",
          {env[k] for k in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")} == {"devflock-openrouter"})
    check("context window comes from the probe when known", env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "64000")
    ep2 = Endpoint(name="m", base_url="http://x", api_key="k", provider=PROVIDER_ANTHROPIC)
    check("context window defaults to 200000", mono.context_tokens(ep2) == "200000")
    os.environ["DEVFLOCK_CONTEXT_TOKENS"] = "32768"
    check("DEVFLOCK_CONTEXT_TOKENS wins", mono.context_tokens(ep2) == "32768")
    del os.environ["DEVFLOCK_CONTEXT_TOKENS"]
    sett = json.dumps(mono.settings_override(ep, port))
    check("settings override carries no real key", "REAL-SECRET-KEY" not in sett and "parent-env-key" not in sett)

    # --- A1: non-interactive, extra args passed through to claude
    home, proj = hostile_home_and_project()
    sp = os.path.join(home, "mono-settings.json"); open(sp, "w").write(sett)
    cmd = mono.build_command(claude, ep, sp, ("-p", "say pong"))
    check("extra args are appended to the claude command line", cmd[-2:] == ["-p", "say pong"])
    r = subprocess.run(cmd, env={**env, "HOME": home}, cwd=proj, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    p = posts()
    check("claude -p exits 0 and prints the model's reply", r.returncode == 0 and "pong" in r.stdout, r.stderr[:300])
    check("hostile settings files did NOT redirect it (our mock saw the request)", len(p) >= 1)
    check("request used our model alias, not 'bogus'/'opus'", p and {x["model"] for x in p} == {"devflock-openrouter"},
          str({x["model"] for x in p}))
    check("request carried the placeholder bearer token only", p and all(x["auth"] == "Bearer devflock-local" and
          not x["x-api-key"] for x in p), str([(x["auth"], x["x-api-key"]) for x in p]))

    # --- A2: interactive UI in a pty (fresh HOME => first-run dialogs; no login prompt may appear)
    LOG.clear()
    home, proj = hostile_home_and_project()
    sp = os.path.join(home, "mono-settings.json"); open(sp, "w").write(sett)
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(proj)
        os.execvpe(claude, mono.build_command(claude, ep, sp, ()), {**env, "HOME": home, "TERM": "xterm-256color"})
    buf = bytearray()
    def pump(t):
        end = time.time() + t
        while time.time() < end:
            if select.select([fd], [], [], 0.2)[0]:
                try: d = os.read(fd, 65536)
                except OSError: return
                if not d: return
                buf.extend(d)
    def screen(): return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", buf.decode("utf8", "replace"))
    pump(8)
    first = screen().lower()
    check("interactive start: no login / API-key prompt", not any(w in first for w in
          ("select login method", "do you want to use this api key", "sign in", "log in to")))
    os.write(fd, b"\r"); pump(3)                                  # theme
    os.write(fd, b"\r"); pump(3)                                  # security notes
    os.write(fd, b"\x1b[B"); pump(1); os.write(fd, b"\r"); pump(5)  # trust this folder: Down -> Yes
    os.write(fd, b"hello there"); pump(1); os.write(fd, b"\r"); pump(10)
    out = screen()
    check("interactive: a typed prompt reaches the endpoint and the reply is shown", len(posts()) >= 1 and "pong" in out)
    check("interactive: header shows our model alias", "devflock-openrouter" in out)
    try: os.kill(pid, 9); os.waitpid(pid, 0)
    except OSError: pass

    # --- A3: run_session lifecycle (worker thread, SIGINT handling, temp settings file) with a stand-in gateway
    LOG.clear()
    class FakeGateway:                       # same interface as devflock.gateway.Gateway, forwards nothing
        def __init__(self, *a, **k): self.stats = type("S", (), {"requests": 0, "failures": 0, "last_error": None})()
        async def start(self): return port
        async def stop(self): pass
    real_gw = mono.Gateway
    mono.Gateway = FakeGateway
    try:
        home, proj = hostile_home_and_project()
        os.environ["HOME"] = home
        rc, _ = asyncio.run(mono.run_session(ep, Path(proj), claude, ("-p", "say pong")))
    finally:
        mono.Gateway = real_gw
    check("run_session: runs claude to completion and returns its exit code", rc == 0 and len(posts()) >= 1, f"rc={rc}")
    left = list(Path(os.environ["DEVFLOCK_CONFIG_DIR"]).glob("mono-settings-*.json"))
    check("run_session: temporary settings file removed afterwards", not left, str(left))
    srv.shutdown()


def part_wiring():
    from click.testing import CliRunner
    import devflock.cli as cli
    calls = []
    real = mono.run_mono
    mono.run_mono = lambda project, refresh, args: calls.append((project, refresh, args)) or 7
    try:
        r = CliRunner().invoke(cli.main, ["--mono", "--project", "/some/dir", "--", "-p", "hi", "--continue"])
        check("devflock --mono routes to mono mode and returns claude's exit code",
              r.exit_code == 7 and calls == [("/some/dir", False, ("-p", "hi", "--continue"))], f"{r.exit_code} {calls} {r.output}")
        calls.clear()
        r = CliRunner().invoke(cli.main, ["--mono", "--refresh", "-p", "hi"])
        check("unknown options after --mono pass through even without `--`",
              calls == [(".", True, ("-p", "hi"))], str(calls))
        calls.clear()
        r = CliRunner().invoke(mono.mono_main, ["--project", "/p", "--", "--resume"])
        check("devflock-mono entry point parses the same way", r.exit_code == 7 and calls == [("/p", False, ("--resume",))],
              f"{r.exit_code} {calls} {r.output}")
        calls.clear()
        r = CliRunner().invoke(cli.main, ["--project", tempfile.mkdtemp(), "--", "stray"])
        check("plain `devflock` rejects stray arguments instead of silently ignoring them",
              r.exit_code == 2 and not calls and "unexpected arguments" in r.output, f"{r.exit_code} {r.output}")
    finally:
        mono.run_mono = real


def part_b():
    try:
        import aiohttp  # noqa: F401
        from aiohttp import web
        from mock_llama_server import make_app
    except ImportError as e:
        print(f"[SKIP] Part B (real gateway) needs aiohttp: {e}")
        return
    port = 18870
    ready = threading.Event()
    def serve():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def start():
            r = web.AppRunner(make_app("sk-mono-real", flaky=False, tool_capable=True)); await r.setup()
            await web.TCPSite(r, "127.0.0.1", port).start(); ready.set()
        loop.run_until_complete(start()); loop.run_forever()
    threading.Thread(target=serve, daemon=True).start(); ready.wait(10)

    home, proj = hostile_home_and_project()
    os.environ["HOME"] = home
    ep = Endpoint(name="mono", base_url=f"http://127.0.0.1:{port}", api_key="sk-mono-real", provider=PROVIDER_ANTHROPIC,
                  model_name="qwen")
    check("anthropic-compatible endpoint: child gets the placeholder, not the real key",
          mono.session_env(ep, 1)["ANTHROPIC_AUTH_TOKEN"] == "devflock-local")
    rc, gw = asyncio.run(mono.run_session(ep, Path(proj), mono.find_claude(), ("-p", "say hi")))
    check("gateway relayed Claude Code's requests", gw.stats.requests >= 1, str(gw.stats))
    check("upstream accepted the key the gateway injected (no failures)", gw.stats.failures == 0 and rc == 0,
          f"rc={rc} stats={gw.stats}")
    left = [f for f in Path(os.environ.get("DEVFLOCK_CONFIG_DIR", Path.home() / ".config" / "devflock")).glob("mono-settings-*.json")]
    check("temporary settings file was removed", not left, str(left))


if __name__ == "__main__":
    part_a()
    part_wiring()
    part_b()
    print("\nALL PASS" if ok else "\nSOME FAILED")
    sys.exit(0 if ok else 1)
