"""Credential store: location, permissions, round trips, dedupe/cap, corrupt-file tolerance."""
import os, stat, sys, tempfile
from pathlib import Path
os.environ["DEVFLOCK_CONFIG_DIR"] = tempfile.mkdtemp(prefix="devflock-store-")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from devflock import credstore as cs

ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(f"[{'PASS' if cond else 'FAIL'}] {name}")

check("nothing saved initially", cs.get_cloudflare() is None and cs.list_endpoints() == [])
cs.save_cloudflare("acct", "tok-secret-123", "gw1")
c = cs.get_cloudflare()
check("cloudflare round trip", c["account_id"] == "acct" and c["token"] == "tok-secret-123" and c["gateway_id"] == "gw1")
if sys.platform != "win32":
    check("file is 0600", stat.S_IMODE(cs.path().stat().st_mode) == 0o600)
    check("folder is 0700", stat.S_IMODE(cs.config_dir().stat().st_mode) == 0o700)
check("no leftover temp file", not cs.path().with_suffix(".tmp").exists())

cs.save_endpoint("https://a.example", "k1"); cs.save_endpoint("https://b.example", "k2", "bearer")
cs.save_endpoint("https://a.example", "k1-rotated")
eps = cs.list_endpoints()
check("same URL replaces (rotated key), newest first", [e["url"] for e in eps] == ["https://a.example", "https://b.example"]
      and eps[0]["key"] == "k1-rotated" and eps[1]["auth_style"] == "bearer")
check("saving an endpoint keeps the cloudflare entry", cs.get_cloudflare() is not None)
for i in range(12): cs.save_endpoint(f"https://x{i}.example", "k")
check(f"endpoint list capped at {cs.MAX_ENDPOINTS}", len(cs.list_endpoints()) == cs.MAX_ENDPOINTS)
cs.forget_endpoint("https://x11.example")
check("forget_endpoint", all(e["url"] != "https://x11.example" for e in cs.list_endpoints()))
cs.forget_cloudflare()
check("forget_cloudflare leaves endpoints", cs.get_cloudflare() is None and cs.list_endpoints())

check("no OpenRouter key saved initially", cs.get_openrouter() is None)
cs.save_cloudflare("acct", "tok-secret-123")
cs.save_openrouter("sk-or-secret-1", "https://me.example", "Me")
o = cs.get_openrouter()
check("openrouter round trip", o["api_key"] == "sk-or-secret-1" and o["site_url"] == "https://me.example"
      and o["site_title"] == "Me")
check("saving the OpenRouter key keeps the cloudflare entry and the endpoints",
      cs.get_cloudflare()["token"] == "tok-secret-123" and cs.list_endpoints())
if sys.platform != "win32":
    check("file is still 0600 with the OpenRouter key in it", stat.S_IMODE(cs.path().stat().st_mode) == 0o600)
cs.forget_openrouter()
check("forget_openrouter leaves cloudflare + endpoints",
      cs.get_openrouter() is None and cs.get_cloudflare() and cs.list_endpoints())
cs.forget_cloudflare()

cs.path().write_text("{ this is not json")
check("corrupt file == nothing saved, no crash", cs.get_cloudflare() is None and cs.list_endpoints() == [])
cs.save_cloudflare("a2", "t2")
check("saving over a corrupt file recovers", cs.get_cloudflare()["token"] == "t2")
check("clear_all deletes the file", cs.clear_all() and not cs.path().exists() and not cs.clear_all())
check("mask hides the middle", cs.mask("cfut_abcdefghijkl") == "cfut…kl" and "abcdefgh" not in cs.mask("cfut_abcdefghijkl"))
print("\nALL CREDSTORE TESTS " + ("PASSED" if ok else "FAILED")); sys.exit(0 if ok else 1)
