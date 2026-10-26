"""Three real wizard runs sharing one config dir:
  run 1  nothing saved -> type creds -> they are verified -> 'save?' yes
  run 2  saved Cloudflare creds + saved manager endpoint are OFFERED and reused; no token typed
  run 3  saved token has gone stale -> detected -> forget -> type a fresh one -> saved again
plus --forget-credentials. Real Claude Code workers run through mock Cloudflare each time."""
import asyncio, json, os, sys, tempfile, threading
from pathlib import Path

CF_PORT, LL_PORT = 18830, 18829
CFG = tempfile.mkdtemp(prefix="devflock-cfg-")
os.environ.update({"DEVFLOCK_CONFIG_DIR": CFG, "CLOUDFLARE_API_BASE": f"http://127.0.0.1:{CF_PORT}/client/v4"})
for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CF_ACCOUNT_ID", "CF_API_TOKEN"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).parent.parent / "src")); sys.path.insert(0, str(Path(__file__).parent))
from aiohttp import web
from click.testing import CliRunner
import mock_cloudflare_server as mcf
from mock_llama_server import make_app as make_llama
import devflock.cli as cli
from devflock import credstore
from devflock.packer import ModuleSpec, Plan, TaskSpec

def serve():
    ready = threading.Event()
    def run():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def start():
            r = web.AppRunner(make_llama("sk-mgr", flaky=False, tool_capable=True)); await r.setup()
            await web.TCPSite(r, "127.0.0.1", LL_PORT).start()
            r2 = web.AppRunner(mcf.make_app()); await r2.setup()
            await web.TCPSite(r2, "127.0.0.1", CF_PORT).start(); ready.set()
        loop.run_until_complete(start()); loop.run_forever()
    threading.Thread(target=run, daemon=True).start(); ready.wait(10)

async def fake_plan(worker, mode, idea, n):
    w = "Use the Write tool to create hello.py containing a print statement."
    return Plan(modules=[ModuleSpec("alpha", path="mod_alpha", weight=2), ModuleSpec("beta", path="mod_beta", weight=1)],
                tasks=[TaskSpec("a1", "alpha", w, acceptance_cmd="test -f hello.py"),
                       TaskSpec("b1", "beta", w, acceptance_cmd="test -f hello.py")]), "Two tiny modules."

ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}")

def wizard(lines):
    project = Path(tempfile.mkdtemp(prefix="devflock-sc-"))
    res = CliRunner().invoke(cli.main, ["--project", str(project)], input="\n".join(lines) + "\n")
    if res.exception and not isinstance(res.exception, SystemExit):
        import traceback; traceback.print_exception(res.exception)
    return res.output, res.exit_code

serve(); cli.plan_fn = fake_plan
TOKEN = "cf-test-token"

# ---- run 1: type everything; each credential is offered for saving the moment it is verified
out, code = wizard([
    "1", f"http://127.0.0.1:{LL_PORT} sk-mgr",      # manager: endpoint URL + key
    "y",                                            # save this endpoint? yes (asked right after it probes OK)
    "1", "2", "", "2",                              # mode 1, 2 workers, no request, accept 2
    "2", "acct123", TOKEN, "y", "3",                # worker 1: Cloudflare -> account, token, save? yes, model #3
    "2", "qwen",                                    # worker 2: Cloudflare (creds reused in-run) -> model by search
    "y"])                                           # start
print("---- run 1 ----"); print(out[-900:])
saved = credstore.get_cloudflare()
check("run 1: finished, all tasks done", code == 0 and "'done': 2" in out)
check("run 1: creds were verified before being offered", "✓ credentials work" in out)
check("run 1: asked once per credential (1 endpoint + 1 Cloudflare), and said where",
      out.count("for next time?") == 2 and "Save this endpoint" in out and "Save them for next time?" in out
      and CFG in out.replace("\n", ""))
check("run 1: token never printed", TOKEN not in out)
check("run 1: Cloudflare creds + manager endpoint saved",
      saved and saved["token"] == TOKEN and [e["url"] for e in credstore.list_endpoints()] == [f"http://127.0.0.1:{LL_PORT}"])

# ---- run 2: reuse
out, code = wizard([
    "1", "1",                                       # manager: endpoint -> saved #1
    "1", "2", "", "2",
    "2", "y", "3",                                  # worker 1: Cloudflare -> use saved? yes -> model #3
    "2", "qwen",                                    # worker 2
    "y"])                                           # start (nothing new, so no save question)
print("---- run 2 ----"); print(out[-700:])
check("run 2: saved endpoint offered and used", "Saved endpoints:" in out and "✓ manager" in out)
check("run 2: saved Cloudflare creds offered, verified, used; token not asked",
      "Saved Cloudflare credentials" in out and "✓ saved credentials work" in out
      and "Cloudflare API token" not in out and "Cloudflare Account ID" not in out)
check("run 2: no save prompt (nothing new)", "for next time?" not in out)
check("run 2: all tasks done", code == 0 and "'done': 2" in out)
check("run 2: token never printed", TOKEN not in out)

# ---- run 3: stale saved token
data = json.loads(credstore.path().read_text()); data["cloudflare"]["token"] = "stale-token"
credstore.path().write_text(json.dumps(data))
out, code = wizard([
    "1", "1",
    "1", "2", "", "2",
    "2", "y", "y", "acct123", TOKEN, "y", "3",      # Cloudflare -> use saved? y -> fails -> forget? y -> type new -> save? y -> model
    "2", "qwen",                                    # worker 2 reuses the fresh creds
    "y"])
print("---- run 3 ----"); print(out[-800:])
check("run 3: stale token detected, not silently used", "no longer work" in out and "stale-token" not in out)
check("run 3: asked for fresh creds, run still completed", "Cloudflare API token" in out and code == 0 and "'done': 2" in out)
check("run 3: fresh token saved over the stale one", credstore.get_cloudflare()["token"] == TOKEN)

# ---- forget
res = CliRunner().invoke(cli.main, ["--forget-credentials"])
check("--forget-credentials deletes the file and exits", res.exit_code == 0 and not credstore.path().exists())
print("\nALL SAVED-CREDS E2E TESTS " + ("PASSED" if ok else "FAILED")); sys.exit(0 if ok else 1)
