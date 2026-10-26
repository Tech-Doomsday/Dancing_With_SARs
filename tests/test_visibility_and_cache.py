"""Manager visibility, tolerant plan parsing, project map, and the 24 h availability cache.
Runs without Claude Code / network; missing third-party libs are stubbed so only logic is tested.
Run: python tests/test_visibility_and_cache.py"""
import asyncio, json, os, sys, tempfile, types
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

os.environ["DEVFLOCK_CONFIG_DIR"] = tempfile.mkdtemp(prefix="devflock-cache-test-")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
for mod in ("aiohttp", "aiohttp.web", "click", "rich", "rich.console", "rich.live", "rich.table",
            "rich.markup", "rich.text", "claude_agent_sdk"):
    try:
        __import__(mod)
    except ImportError:
        sys.modules[mod] = MagicMock()
if isinstance(sys.modules.get("rich.markup"), MagicMock):
    sys.modules["rich.markup"].escape = lambda s: s

from devflock import cache, planner, digest
from devflock.planner import extract_json, make_plan, validate_plan, PlanningFailed
import devflock.cli as cli
from devflock import trace

ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}")

GOOD = {"vision": "A hazard engine with sixteen pipelines; three are stubs.", "summary": "Split per pipeline.",
        "modules": [{"name": "flood", "path": "src/flood", "weight": 2},
                    {"name": "quake", "path": "src/quake", "weight": 2}],
        "tasks": [{"id": "t1", "module": "flood", "description": "Finish the flood pipeline stages.", "acceptance_cmd": "pytest -q"},
                  {"id": "t2", "module": "quake", "description": "Finish the quake pipeline stages.", "acceptance_cmd": "pytest -q"}]}

# --- tolerant extraction (the real-world failure: several {...} spans in one multi-turn reply)
noisy = ('I will look at {"file": "a.py"} first.\n{"name": "Glob", "input": {"pattern": "*.py"}}\n'
         'Now the answer:\n```json\n' + json.dumps(GOOD) + '\n```\nHope it helps {not json}')
check("picks the plan among unrelated JSON objects", extract_json(noisy)["summary"] == "Split per pipeline.")
check("unwraps {'plan': {...}}", extract_json(json.dumps({"plan": GOOD}))["modules"][0]["name"] == "flood")
check("braces inside strings do not break scanning",
      extract_json('x {"summary": "uses } and { chars", "modules": [{"name":"a"}], "tasks": [{"id":"t"}]} y')["summary"].startswith("uses"))
tool_call_only = json.dumps({"name": "Read", "input": {"file_path": "x"}})
_, errs = validate_plan(extract_json(tool_call_only))
check("non-plan JSON still reports the modules/tasks problem", any("modules" in e for e in errs))

# --- project map
proj = Path(tempfile.mkdtemp(prefix="proj-"))
(proj / "ARCHITECTURE.md").write_text("# Hazard engine\nSixteen pipelines share a cache layer.\n")
(proj / "src" / "flood").mkdir(parents=True)
(proj / "src" / "flood" / "run.py").write_text('"""Flood detection from SAR backscatter."""\nprint(1)\n')
(proj / "data").mkdir(); [(proj / "data" / f"s{i}.tif").write_bytes(b"x") for i in range(5)]
(proj / "node_modules").mkdir(); (proj / "node_modules" / "junk.js").write_text("x")
d = digest.build_digest(proj)
check("map includes ARCHITECTURE text", "Sixteen pipelines share a cache layer" in d)
check("map includes module docstring", "Flood detection from SAR backscatter" in d)
check("map counts data files instead of listing them", "s3.tif" not in d and "5 other/data files" in d)
check("map skips node_modules", "junk.js" not in d)

# --- planner: map in prompt, tools only on attempt 1, feedback names what the model said
class Fake:
    def __init__(self, replies, root): self.replies, self.calls, self.region_dir = list(replies), [], root
    async def run_task(self, prompt, *, max_turns=0, allowed_tools=None, resume=False, timeout_s=0):
        self.calls.append((prompt, allowed_tools, max_turns, resume))
        return NS(ok=True, summary=self.replies.pop(0), error=None)
f = Fake(["Here is my analysis of the project, it looks great.", json.dumps({"plan": "nested string"}), json.dumps(GOOD)], proj)
plan, summary = asyncio.run(make_plan(f, "existing", "complete the sub sections semi independently", 2))
check("recovers on the 3rd reply", plan and summary == "Split per pipeline.")
check("vision is kept on the plan", plan.vision.startswith("A hazard engine"))
check("attempt 1 prompt carries the project map", "<project-map>" in f.calls[0][0] and "Sixteen pipelines" in f.calls[0][0])
check("attempt 1 may explore, later attempts may not", f.calls[0][1] == ["Read", "Glob", "Grep"] and f.calls[1][1] == [] and f.calls[2][1] == [])
check("feedback quotes the unusable reply", "Here is my analysis" in f.calls[1][0])
check("user's request + 'independently' guidance reach the manager", "semi independently" in f.calls[0][0] and "own module" in f.calls[0][0])
f = Fake(["nope"] * 3, proj)
try: asyncio.run(make_plan(f, "existing", None)); check("failure message shows the manager's reply", False)
except PlanningFailed as e: check("failure message shows the manager's reply", "nope" in str(e))

# --- tracer: grey prompts, folded map on screen, full text in the log
log = Path(tempfile.mkdtemp()) / "transcript.log"
printed = []
t = trace.Tracer(NS(print=lambda *a, **k: printed.append(a)), log)
t("manager", "prompt", "intro\n" + planner.wrap_for_prompt("LINE\n" * 50) + "\nrules")
t("manager", "tool", "Read ARCHITECTURE.md"); t("manager", "reply", "hello")
body = log.read_text()
check("transcript keeps the full map", body.count("LINE") == 50 and "manager  prompt" in body and "Read ARCHITECTURE.md" in body)
check("console prompt folds the map", "folded here" in trace.fold_project_map("a\n<project-map>\nx\ny\n</project-map>\nb"))
check("something was printed for each event", len(printed) >= 5)
printed.clear(); trace.Tracer(NS(print=lambda *a, **k: printed.append(a)), None, show_prompts=False)("m", "prompt", "secret")
check("--hide-prompts prints nothing for prompts", not printed)

# --- 24 h cache
cache.put("s", "k", {"a": 1}); check("cache hit", cache.get("s", "k") == {"a": 1})
cache.set_force_refresh(True); check("--refresh bypasses cache", cache.get("s", "k") is None); cache.set_force_refresh(False)
data = json.loads(cache.path().read_text()); data["s"]["k"]["at"] -= 25 * 3600; cache.path().write_text(json.dumps(data))
check("entries older than 24 h expire", cache.get("s", "k") is None)
check("cache never stores the token", "tok-secret" not in cache.key_id("cf", "acct", "tok-secret"))

# --- Cloudflare availability: checked once per run, then not for 24 h
calls = {"plan": 0, "refine": 0}
async def fake_detect(creds): calls["plan"] += 1; return True
async def fake_refine(creds, models):
    calls["refine"] += 1
    for m in models: m.available = m.id != "@cf/gated/model"
cli.cf.detect_free_plan, cli.cf.refine_availability = fake_detect, fake_refine
cli.console = NS(print=lambda *a, **k: None, status=lambda *a, **k: types.SimpleNamespace(__enter__=lambda s: s, __exit__=lambda s, *a: None))
class _St:
    def __enter__(self): return self
    def __exit__(self, *a): return False
cli.console.status = lambda *a, **k: _St()
creds = cli.cf.CfCreds("acct1", "tok-secret", "")
def fresh_run():
    cli._cf_models = [cli.cf.CfModel("@cf/gated/model"), cli.cf.CfModel("@cf/open/model")]
    cli._cf_free_plan, cli._cf_plan_checked, cli._cf_avail_applied, cli._cf_avail, cli._cf_avail_kid = None, False, False, {}, None
fresh_run()
cli._check_availability(creds); cli._check_availability(creds); cli._check_availability(creds)   # manager + 2 workers
check("one run: plan check + model probes happen once, not per worker", calls == {"plan": 1, "refine": 1})
check("gated model is greyed", [m.available for m in cli._cf_models] == [False, True])
fresh_run(); cli._check_availability(creds)
check("next run within 24 h: zero requests", calls == {"plan": 1, "refine": 1})
check("cached result still greys the gated model", [m.available for m in cli._cf_models] == [False, True])
check("hand-typed gated id answered from cache", cli._model_unavailable("@cf/gated/model") is True)
cache.set_force_refresh(True); fresh_run(); cli._check_availability(creds); cache.set_force_refresh(False)
check("--refresh redoes the check", calls == {"plan": 2, "refine": 2})
fresh_run(); cli._check_availability(cli.cf.CfCreds("acct1", "other-token", ""))
check("a different token is not served from cache", calls == {"plan": 3, "refine": 3})

# --- endpoint smoke-test cache
ep = cli.Endpoint(name="m", base_url="u", api_key="k1", model_name="vendor/x", provider="openrouter")
check("nothing cached at first", cli._probe_cached(ep) is None)
cli._probe_store(ep, cli.ProbeResult(True, auth_style="bearer", latency_s=1.5, tools_ok=True))
r, when = cli._probe_cached(ep)
check("passing probe reused", r.ok and r.tools_ok and r.latency_s == 1.5)
cli._probe_store(cli.Endpoint("m", "u", "k2", model_name="v/y", provider="openrouter"), cli.ProbeResult(False, error="x"))
check("failed probes are never cached", cli._probe_cached(cli.Endpoint("m", "u", "k2", model_name="v/y", provider="openrouter")) is None)
own = cli.Endpoint("m", "http://tunnel", "k")
cli._probe_store(own, cli.ProbeResult(True)); check("own tunnels are never cached", cli._probe_cached(own) is None)

# --- the failure seen in the wild: Claude Code returns "API Error: 400 ..." as assistant TEXT, subtype=success
check("planner stops at once on a 400 from the model endpoint", True)
class Err400:
    calls = 0
    async def run_task(self, prompt, **k):
        Err400.calls += 1
        return NS(ok=False, summary="API Error: 400 {...}", error="API Error: 400 Unable to determine provider for model 'devflock-cloudflare'")
try: asyncio.run(make_plan(Err400(), "idea", "x")); check("fails fast on API Error 400", False)
except PlanningFailed as e: check("fails fast on API Error 400 (1 attempt, message kept)", Err400.calls == 1 and "Unable to determine provider" in str(e))

fake = types.ModuleType("claude_agent_sdk")
class _TB:  
    def __init__(s, text): s.text = text
class _TU:
    def __init__(s, name, input): s.name, s.input = name, input
class _AM:
    def __init__(s, content): s.content = content
class _RM:
    subtype, session_id, usage, total_cost_usd, num_turns = "success", "s", {}, 0, 1
class _Opt:
    def __init__(s, **k): _Opt.last = k
class _Client:
    def __init__(s, options=None): pass
    async def __aenter__(s): return s
    async def __aexit__(s, *a): return False
    async def query(s, p): pass
    async def receive_response(s):
        yield _AM([_TB(_Client.reply)]); yield _RM()
for k, v in dict(TextBlock=_TB, ToolUseBlock=_TU, AssistantMessage=_AM, ResultMessage=_RM, ClaudeAgentOptions=_Opt,
                 ClaudeSDKClient=_Client, HookMatcher=_Opt, ProcessError=Exception, CLIConnectionError=Exception).items():
    setattr(fake, k, v)
sys.modules["claude_agent_sdk"] = fake
import importlib
from devflock import worker, observe
worker = importlib.reload(worker)      # rebind to the fake SDK (cli imported it with a stub earlier)
events = []; observe.set_observer(lambda w, k, t: events.append((w, k, t)))
wk = worker.Worker("manager", 1, "k", tempfile.gettempdir())
_Client.reply = "API Error: 400 {\"error\":{\"message\":\"Unable to determine provider for model\"}}"
r = asyncio.run(wk.run_task("hello"))
check("'API Error:' text is a failure, not a reply", not r.ok and r.error.startswith("API Error: 400"))
check("failure is announced to the observer", any(k == "error" for _, k, _ in events))
_Client.reply = "All good."
check("normal text is still a success", asyncio.run(wk.run_task("hi")).ok)
check("sessions ignore user/project Claude Code settings", _Opt.last.get("setting_sources") == [])
check("prompt reaches the observer", ("manager", "prompt", "hello") in events)
observe.set_observer(lambda *a: 1 / 0); check("a crashing observer cannot break a run", asyncio.run(wk.run_task("hi")).ok)

print("\nALL VISIBILITY/CACHE TESTS " + ("PASSED" if ok else "FAILED")); sys.exit(0 if ok else 1)
