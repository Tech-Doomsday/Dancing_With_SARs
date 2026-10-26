"""Providers mix freely inside ONE real wizard run:
   manager  = OpenRouter
   worker 1 = Cloudflare Workers AI
   worker 2 = OpenRouter (a different model than the manager)
Both mocks run side by side; each worker's real Claude Code session must reach its own provider only,
with its own credential. Credentials come from env vars.
Run: python tests/test_cli_mixed_e2e.py
"""
import asyncio, os, sys, tempfile, threading, subprocess
os.environ["DEVFLOCK_CONFIG_DIR"] = tempfile.mkdtemp(prefix="devflock-mix-cfg-")
from pathlib import Path

CF_PORT, OR_PORT = 18871, 18872
os.environ["CLOUDFLARE_API_BASE"] = f"http://127.0.0.1:{CF_PORT}/client/v4"
os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acct123"
os.environ["CLOUDFLARE_API_TOKEN"] = "cf-test-token"
os.environ["OPENROUTER_API_BASE"] = f"http://127.0.0.1:{OR_PORT}/api/v1"
os.environ["OPENROUTER_API_KEY"] = "sk-or-test-key"
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from aiohttp import web
from click.testing import CliRunner
import mock_cloudflare_server as mcf
import mock_openrouter_server as mor
import devflock.cli as cli
from devflock import cloudflare as cf
from devflock.packer import ModuleSpec, Plan, TaskSpec

APPS = {}


def serve():
    ready = threading.Event()
    def run():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def start():
            APPS["cf"] = mcf.make_app(); r = web.AppRunner(APPS["cf"]); await r.setup()
            await web.TCPSite(r, "127.0.0.1", CF_PORT).start()
            APPS["or"] = mor.make_app(); r2 = web.AppRunner(APPS["or"]); await r2.setup()
            await web.TCPSite(r2, "127.0.0.1", OR_PORT).start()
            ready.set()
        loop.run_until_complete(start()); loop.run_forever()
    threading.Thread(target=run, daemon=True).start(); ready.wait(10)


async def fake_plan(worker, mode, idea, n):
    write = "Use the Write tool to create hello.py containing a print statement."
    return Plan(
        modules=[ModuleSpec("alpha", path="mod_alpha", weight=2), ModuleSpec("beta", path="mod_beta", weight=1)],
        tasks=[TaskSpec("a1", "alpha", write, acceptance_cmd="test -f hello.py"),
               TaskSpec("b1", "beta", write, acceptance_cmd="test -f hello.py")]), "Two tiny modules."


def main():
    serve()
    cli.plan_fn = fake_plan
    project = Path(tempfile.mkdtemp(prefix="devflock-mixe2e-"))
    lines = [
        "3", "sonnet",                      # manager: OpenRouter, model by search
        "1", "2", "", "2",                  # mode 1, two workers, no request, accept 2
        "2", "gpt-oss",                     # worker 1: Cloudflare, model by search (unique)
        "3", "gpt-oss",                     # worker 2: OpenRouter, model by search (unique)
        "y",                                # start
    ]
    res = CliRunner().invoke(cli.main, ["--project", str(project)], input="\n".join(lines) + "\n")
    print(res.output[-1800:])
    if res.exception and not isinstance(res.exception, SystemExit):
        import traceback; traceback.print_exception(res.exception)
    ok = True
    def check(name, cond):
        nonlocal ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    out = res.output
    check("wizard exited cleanly, all tasks done", res.exit_code == 0 and "'done': 2" in out)
    check("both credentials came from the environment, neither printed",
          "using Cloudflare credentials from environment" in out and "OPENROUTER_API_KEY environment variable" in out
          and "cf-test-token" not in out and "sk-or-test-key" not in out)
    cf_models = {r["model"] for r in APPS["cf"]["requests"]} - cf.PAID_PLAN_MODELS
    or_models = {r["model"] for r in APPS["or"]["requests"]}
    check(f"Cloudflare saw only its worker's model: {sorted(cf_models)}", cf_models == {"@cf/openai/gpt-oss-120b"})
    check(f"OpenRouter saw the manager's model (its connection probe) and worker 2's, nothing else: {sorted(or_models)}",
          or_models == {"anthropic/claude-sonnet-4.5", "openai/gpt-oss-120b"})
    check("each provider got only its own credential",
          all(h.get("Authorization") == "Bearer cf-test-token" for h in APPS["cf"]["headers"])
          and all(h.get("Authorization") == "Bearer sk-or-test-key" for h in APPS["or"]["headers"]))
    def on_branch(path):
        return subprocess.run(["git", "cat-file", "-e", f"devflock/integration:{path}"], cwd=project).returncode == 0
    check("both workers' files merged onto the integration branch",
          on_branch("mod_alpha/hello.py") and on_branch("mod_beta/hello.py"))
    log = (project / ".devflock" / "devflock.log").read_text()
    check("neither credential written to the project's log", "cf-test-token" not in log and "sk-or-test-key" not in log)
    print("\nALL CLI MIXED-PROVIDER E2E TESTS " + ("PASSED" if ok else "FAILED")); sys.exit(0 if ok else 1)


main()
