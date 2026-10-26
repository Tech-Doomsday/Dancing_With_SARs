"""Drive the REAL wizard with OpenRouter as a provider choice. Real Claude Code runs the workers,
each through the translating gateway to a mock OpenRouter.

Part A (key from the OPENROUTER_API_KEY env var, never prompted):
   manager  = OpenRouter, model picked by SEARCH TEXT
   worker 1 = "same as the manager" (the default answer)
   worker 2 = OpenRouter, a PASTED id that is not in the model list (~openai/gpt-sol-latest)
Part B (no env var; three wizard runs sharing one config dir):
   run 1  type the key (hidden) -> verified -> saved
   run 2  saved key offered, re-verified, reused; nothing typed
   run 3  saved key gone stale -> detected -> forget -> type a fresh one -> saved over the stale one
   then --forget-credentials.
Run: python tests/test_cli_openrouter_e2e.py
"""
import asyncio, json, os, sys, tempfile, threading, subprocess
CFG = tempfile.mkdtemp(prefix="devflock-or-cfg-")
os.environ["DEVFLOCK_CONFIG_DIR"] = CFG
from pathlib import Path

OR_PORT, LL_PORT = 18861, 18860
os.environ["OPENROUTER_API_BASE"] = f"http://127.0.0.1:{OR_PORT}/api/v1"
KEY = "sk-or-test-key"
os.environ["OPENROUTER_API_KEY"] = KEY
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from aiohttp import web
from click.testing import CliRunner
import mock_openrouter_server as mor
from mock_llama_server import make_app as make_llama
import devflock.cli as cli
from devflock import credstore
from devflock.packer import ModuleSpec, Plan, TaskSpec

APPS = {}


def serve():
    ready = threading.Event()
    def run():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        async def start():
            r = web.AppRunner(make_llama("sk-mgr", flaky=False, tool_capable=True)); await r.setup()
            await web.TCPSite(r, "127.0.0.1", LL_PORT).start()
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


ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def wizard(lines, project=None):
    project = project or Path(tempfile.mkdtemp(prefix="devflock-ore2e-"))
    res = CliRunner().invoke(cli.main, ["--project", str(project)], input="\n".join(lines) + "\n")
    if res.exception and not isinstance(res.exception, SystemExit):
        import traceback; traceback.print_exception(res.exception)
    return res, project


def main():
    serve()
    cli.plan_fn = fake_plan
    SONNET, ALIAS = "anthropic/claude-sonnet-4.5", "~openai/gpt-sol-latest"

    # ------------------------------------------------------------------ Part A
    res, project = wizard([
        "3",                                # manager provider: 3) OpenRouter
        "sonnet",                           #   model by search text (unique match)
        "1", "2", "",                       # mode 1, two workers, no specific request
        "2",                                # accept 2 workers
        "",                                 # worker 1: default = 4) same as the manager
        "3",                                # worker 2 provider: OpenRouter
        ALIAS,                              #   pasted id that is not in the list
        "y",                                # start
    ])
    out = res.output
    print("---- part A ----"); print(out[-2400:])
    check("A: wizard exited cleanly", res.exit_code == 0)
    check("A: menu offers OpenRouter as 3 and 'same as the manager' as 4",
          "3) OpenRouter (pick a model)" in out and "4) same as the manager" in out)
    check("A: key taken from the environment (never prompted)", "OpenRouter API key" not in out
          and "OPENROUTER_API_KEY environment variable" in out)
    check("A: key never printed", KEY not in out)
    check("A: model table shows newest tool-capable models, with price + free marker",
          "claude-sonnet-4.5" in out and "$3/$15" in out and "free" in out and "llama-3.2-1b" not in out
          and "gemini-image-gen" not in out)
    check("A: pasted id outside the list is accepted with a note", "not in the model list" in out)
    check("A: 'same as the manager' says it reuses the OpenRouter model", "same OpenRouter model" in out)
    check("A: all tasks done", "'done': 2" in out)
    used = {r["model"] for r in APPS["or"]["requests"]}
    check(f"A: workers ran their own models (manager's reused by worker 1, alias by worker 2): {sorted(used)}",
          used == {SONNET, ALIAS})
    check("A: every request authenticated with the real key and carried the DevFlock title",
          all(h.get("Authorization") == f"Bearer {KEY}" and h.get("X-OpenRouter-Title") == "DevFlock"
              for h in APPS["or"]["headers"]))
    def on_branch(path):
        return subprocess.run(["git", "cat-file", "-e", f"devflock/integration:{path}"], cwd=project).returncode == 0
    check("A: both workers' files merged onto the integration branch",
          on_branch("mod_alpha/hello.py") and on_branch("mod_beta/hello.py"))
    check("A: key not written to the project's log", KEY not in (project / ".devflock" / "devflock.log").read_text())
    check("A: env key was never saved to disk", credstore.get_openrouter() is None)

    # ------------------------------------------------------------------ Part B
    del os.environ["OPENROUTER_API_KEY"]
    common = ["1", f"http://127.0.0.1:{LL_PORT} sk-mgr", "n",       # manager: Anthropic-compatible, don't save it
              "1", "2", "", "2"]                                    # mode 1, two workers, no request, accept 2

    res, _ = wizard(common + ["3", KEY, "y", "2",                   # worker 1: OpenRouter -> key, save? yes, model #2
                              "3", "coder",                         # worker 2: key reused in-run -> model by search
                              "y"])
    out = res.output
    print("---- part B run 1 ----"); print(out[-900:])
    saved = credstore.get_openrouter()
    check("B1: finished, all tasks done", res.exit_code == 0 and "'done': 2" in out)
    check("B1: key verified before being offered for saving", "✓ key works" in out and "Save it for next time?" in out)
    check("B1: asked once only (second worker reuses the key)", out.count("OpenRouter API key") == 1)
    check("B1: key never printed", KEY not in out)
    check("B1: key saved", saved and saved["api_key"] == KEY)
    check("B1: saved file is private (0600)", sys.platform == "win32" or (credstore.path().stat().st_mode & 0o777) == 0o600)

    res, _ = wizard(common + ["3", "y", "2",                        # worker 1: OpenRouter -> use saved? yes -> model #2
                              "3", "coder", "y"])
    out = res.output
    print("---- part B run 2 ----"); print(out[-600:])
    check("B2: saved key offered (masked), verified and used; nothing typed",
          "Saved OpenRouter key:" in out and "✓ saved key works" in out and "OpenRouter API key" not in out)
    check("B2: no OpenRouter save prompt (the key is not new)", "Save it for next time?" not in out)
    check("B2: all tasks done, key never printed", res.exit_code == 0 and "'done': 2" in out and KEY not in out)

    data = json.loads(credstore.path().read_text()); data["openrouter"]["api_key"] = "stale-key"
    credstore.path().write_text(json.dumps(data))
    res, _ = wizard(common + ["3", "y", "y", KEY, "y", "2",         # use saved? y -> fails -> forget? y -> type new -> save? y -> model
                              "3", "coder", "y"])
    out = res.output
    print("---- part B run 3 ----"); print(out[-700:])
    check("B3: stale key detected, not silently used", "no longer works" in out and "stale-key" not in out)
    check("B3: asked for a fresh key, run still completed", "OpenRouter API key" in out and res.exit_code == 0 and "'done': 2" in out)
    check("B3: fresh key saved over the stale one", credstore.get_openrouter()["api_key"] == KEY)

    res = CliRunner().invoke(cli.main, ["--forget-credentials"])
    check("--forget-credentials deletes the file (OpenRouter key included) and exits",
          res.exit_code == 0 and not credstore.path().exists())

    print("\nALL CLI OPENROUTER E2E TESTS " + ("PASSED" if ok else "FAILED")); sys.exit(0 if ok else 1)


main()
