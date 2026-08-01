"""Test _load_current_version() — robust installed-version lookup (2026.08.002-c1).

Covers the fix for the false update-available notification: in the Docker
container the VERSION file was never copied into the image, so the old
single-path lookup raised and the fallback returned "0.0.0", making
/api/version/changes believe every release was newer than the installed one.

The fixed lookup tries, in order: APP_VERSION env var, repo-root VERSION
file, cwd VERSION file, /app/VERSION (container layout), then the
verCurrent value baked into public/index.html. "0.0.0" is the last resort.

Run from the repo root:
    /tmp/cwt-venv/bin/python tests/test_version_load.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

EXPECTED = "2026.08.002-c1"

# Isolated DB for the endpoint tests (read at import time by src/app.py).
_TMP = Path(tempfile.mkdtemp(prefix="cwt_verload_"))
os.environ["DB_PATH"] = str(_TMP / "wallets.db")
os.environ.pop("APP_VERSION", None)

import app as app_module  # the REAL module under test
from fastapi.testclient import TestClient

_real_Path = Path

# ── Path redirect shim (test scaffolding only) ───────────────────────────
class _RedirectPath:
    """Stand-in for pathlib.Path redirecting selected absolute prefixes.

    Installed as app_module.Path while a test runs. Paths starting with one
    of the redirect prefixes are rewritten into a temp dir; everything else
    delegates to the real pathlib.Path. Path.cwd() delegates to real cwd.
    """

    def __init__(self, redirects):
        self._redirects = redirects  # {prefix: replacement_dir}

    def __call__(self, *args):
        p = _real_Path(*args)
        s = str(p)
        for prefix, repl in self._redirects.items():
            if s == prefix or s.startswith(prefix + "/"):
                return _real_Path(repl) / s[len(prefix):].lstrip("/")
        return p

    def cwd(self):
        return _real_Path.cwd()


# ── Pure function tests (normal repo layout) ─────────────────────────────
def test_repo_layout_returns_version():
    os.environ.pop("APP_VERSION", None)
    assert app_module._load_current_version() == EXPECTED


def test_coherence_current_matches_vercurrent():
    """current (server) must equal verCurrent (frontend) and the VERSION file."""
    index_html = Path(REPO_ROOT) / "public" / "index.html"
    m = re.search(r'<strong id="verCurrent">([^<]+)</strong>', index_html.read_text(encoding="utf-8"))
    assert m is not None, "verCurrent element not found in public/index.html"
    ver_current = m.group(1).strip()
    version_file = Path(REPO_ROOT) / "VERSION"
    assert app_module._load_current_version() == ver_current == version_file.read_text().strip() == EXPECTED


def test_app_version_env_wins_over_repo_file():
    os.environ["APP_VERSION"] = "2026.08.001"
    try:
        # Env is the most reliable source — it must beat the repo VERSION file.
        assert app_module._load_current_version() == "2026.08.001"
    finally:
        os.environ.pop("APP_VERSION", None)


def test_app_version_zero_zero_is_rejected():
    """A stale "0.0.0" must NEVER be trusted as the installed version."""
    os.environ["APP_VERSION"] = "0.0.0"
    try:
        # "0.0.0" is non-conforming (regex needs YYYY.MM.NNN) -> skipped, real
        # sources are used instead. The false update prompt cannot come back.
        assert app_module._load_current_version() == EXPECTED
    finally:
        os.environ.pop("APP_VERSION", None)


# ── Container simulation (subprocess: cwd != repo, __file__ elsewhere) ────
_CHILD_SCRIPT = r"""
import json, os, sys
from pathlib import Path

cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["tmp_proj"] + "/src")
import app  # fresh interpreter: __file__ lives under tmp_proj

real_Path = Path

class _RedirectPath:
    def __init__(self, redirects):
        self._redirects = redirects
    def __call__(self, *args):
        p = real_Path(*args)
        s = str(p)
        for prefix, repl in self._redirects.items():
            if s == prefix or s.startswith(prefix + "/"):
                return real_Path(repl) / s[len(prefix):].lstrip("/")
        return p
    def cwd(self):
        return real_Path.cwd()

# Container-like context: cwd is NOT the repo, __file__ lives elsewhere,
# /app maps to an empty scratch dir (VERSION absent from the image).
os.chdir(cfg["tmp_cwd"])
app.Path = _RedirectPath({"/app": cfg["tmp_app"]})

def run(**env):
    os.environ.pop("APP_VERSION", None)
    for k, v in env.items():
        os.environ[k] = v
    try:
        return app._load_current_version()
    except Exception as e:  # noqa: BLE001
        return "EXC:" + repr(e)

results = {}

# a. BEFORE (bug reproduction): no APP_VERSION, no VERSION anywhere the
#    container can see -> old code returned "0.0.0" (false update prompt).
results["a_nosources"] = run()

# b. AFTER (env var): APP_VERSION injected at build time.
results["b_env"] = run(APP_VERSION="2026.08.002-c1")

# c. AFTER (repo layout): VERSION copied next to src (parent of src/).
Path(cfg["tmp_proj"], "VERSION").write_text("2026.08.002-c1\n")
results["c_repo_version"] = run()
Path(cfg["tmp_proj"], "VERSION").unlink()

# d. VERSION file in the current working directory.
Path(cfg["tmp_cwd"], "VERSION").write_text("2026.08.001\n")
results["d_cwd_version"] = run()
Path(cfg["tmp_cwd"], "VERSION").unlink()

# e. /app/VERSION (Docker image layout — COPY VERSION /app/VERSION).
Path(cfg["tmp_app"], "VERSION").write_text("2026.08.002-c1\n")
results["e_app_version"] = run()
Path(cfg["tmp_app"], "VERSION").unlink()

# f. Last server-side resort: verCurrent baked into public/index.html.
pub = Path(cfg["tmp_proj"], "public")
pub.mkdir(exist_ok=True)
Path(pub, "index.html").write_text('<html><body><strong id="verCurrent">2026.08.001</strong></body></html>')
results["f_index_fallback"] = run()
Path(pub, "index.html").unlink()

# g. Non-conforming content is skipped; next source wins.
Path(cfg["tmp_proj"], "VERSION").write_text("not-a-version\n")
Path(cfg["tmp_app"], "VERSION").write_text("2026.08.002-c1\n")
results["g_garbage_skipped"] = run()
Path(cfg["tmp_app"], "VERSION").unlink()

# g2. All sources missing or non-conforming -> "0.0.0" (real last resort).
Path(cfg["tmp_proj"], "VERSION").write_text("v1.2.3\n")
results["g2_nonconforming_last_resort"] = run()
Path(cfg["tmp_proj"], "VERSION").unlink()

# h. Content is stripped before validation.
Path(cfg["tmp_proj"], "VERSION").write_text("  2026.08.002-c1  \n")
results["h_strip"] = run()
Path(cfg["tmp_proj"], "VERSION").unlink()

print(json.dumps(results))
"""


def _run_container_simulation():
    """Run the container-like scenarios in a fresh interpreter (isolated sys.modules/cwd)."""
    tmp_proj = Path(tempfile.mkdtemp(prefix="cwt_proj_"))
    tmp_cwd = Path(tempfile.mkdtemp(prefix="cwt_cwd_"))
    tmp_app = Path(tempfile.mkdtemp(prefix="cwt_app_"))
    shutil.copytree(os.path.join(REPO_ROOT, "src"), tmp_proj / "src")
    cfg = {"tmp_proj": str(tmp_proj), "tmp_cwd": str(tmp_cwd), "tmp_app": str(tmp_app)}
    env = {k: v for k, v in os.environ.items() if k != "APP_VERSION"}
    env["DB_PATH"] = str(_TMP / "wallets_child.db")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, json.dumps(cfg)],
        capture_output=True, text=True, timeout=120, cwd=REPO_ROOT, env=env,
    )
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_container_simulation():
    r = _run_container_simulation()
    # a. BEFORE: bug reproduction — nothing findable -> "0.0.0"
    assert r["a_nosources"] == "0.0.0", r
    # b. APP_VERSION env var is the most reliable source in a container.
    assert r["b_env"] == EXPECTED, r
    # c. VERSION file at repo root (parent of src/) still works.
    assert r["c_repo_version"] == EXPECTED, r
    # d. VERSION file in the cwd works.
    assert r["d_cwd_version"] == "2026.08.001", r
    # e. COPY VERSION /app/VERSION works.
    assert r["e_app_version"] == EXPECTED, r
    # f. verCurrent fallback works.
    assert r["f_index_fallback"] == "2026.08.001", r
    # g. garbage skipped, next source used.
    assert r["g_garbage_skipped"] == EXPECTED, r
    # g2. truly nothing valid -> "0.0.0" last resort.
    assert r["g2_nonconforming_last_resort"] == "0.0.0", r
    # h. whitespace stripped.
    assert r["h_strip"] == EXPECTED, r


# ── Real endpoint tests (TestClient + monkeypatched GitHub API) ──────────
FAKE_RELEASES = [
    {
        "tag_name": "2026.08.002-c1", "name": "2026.08.002-c1", "body": "fix: version lookup",
        "published_at": "2026-08-01T12:00:00Z",
        "html_url": "https://github.com/LostInTheBugs/Crypto-Wallet-Tracker/releases/tag/2026.08.002-c1",
    },
    {
        "tag_name": "2026.08.002", "name": "2026.08.002", "body": "feat: update client",
        "published_at": "2026-08-01T10:00:00Z",
        "html_url": "https://github.com/LostInTheBugs/Crypto-Wallet-Tracker/releases/tag/2026.08.002",
    },
]


async def _fake_fetch_releases_cached(url: str):
    if url.endswith("/releases/latest"):
        return FAKE_RELEASES[0]
    return FAKE_RELEASES


def test_endpoints_version_changes_and_update():
    old_fetch = app_module._fetch_releases_cached
    old_path = app_module.Path
    old_update_cfg = app_module._UPDATE_CONFIG_PATH
    app_module._fetch_releases_cached = _fake_fetch_releases_cached
    tmp_data = Path(tempfile.mkdtemp(prefix="cwt_data_"))
    app_module.Path = _RedirectPath({"/data": str(tmp_data)})
    # _UPDATE_CONFIG_PATH is a module-level constant computed at import time
    # (real /data path) — point it at the scratch dir for this test.
    app_module._UPDATE_CONFIG_PATH = _real_Path(tmp_data) / "deploy" / "config.json"
    try:
        with TestClient(app_module.app) as c:
            c.post("/api/auth/register", json={"username": "versetest", "password": "test1234"})
            r = c.post("/api/auth/login", json={"username": "versetest", "password": "test1234"})
            assert r.status_code == 200, r.text

            # Non-regression: /api/health stays public and reports the real version.
            os.environ["APP_VERSION"] = EXPECTED
            h = c.get("/api/health").json()
            assert h["version"] == EXPECTED, h

            # installed == latest -> "à jour": update_available false, count 0.
            d = c.get("/api/version/changes").json()
            assert d["current"] == EXPECTED, d
            assert d["latest"] == "2026.08.002-c1", d
            assert d["update_available"] is False, d
            assert d["count"] == 0, d
            assert d["releases"] == [], d

            # Pre-fix simulation (installed read as "0.0.0") -> false positive,
            # exactly as observed live on the deployed container.
            old_load = app_module._load_current_version
            app_module._load_current_version = lambda: "0.0.0"
            try:
                d = c.get("/api/version/changes").json()
            finally:
                app_module._load_current_version = old_load
            assert d["current"] == "0.0.0", d
            assert d["update_available"] is True, d
            assert d["count"] == 2, d
            assert d["releases"][0]["tag_name"] == "2026.08.002-c1", d
            assert d["releases"][1]["tag_name"] == "2026.08.002", d

            # Non-regression: /api/version/latest unchanged.
            l = c.get("/api/version/latest").json()
            assert l["tag"] == "2026.08.002-c1", l

            # Non-regression: POST /api/update writes the deploy request file.
            r = c.post("/api/update")
            assert r.status_code == 200 and r.json()["ok"] is True, r.text
            req = tmp_data / "deploy" / "request.json"
            assert req.exists(), "deploy request file not written"
            assert json.loads(req.read_text())["target"] == "origin/main"

            # Non-regression: auto/manual toggle unchanged (shared config file).
            r = c.put("/api/settings/update-mode", json={"mode": "auto"})
            assert r.status_code == 200 and r.json()["update_mode"] == "auto", r.text
            assert (tmp_data / "deploy" / "config.json").exists()
            r = c.put("/api/settings/update-mode", json={"mode": "manual"})
            assert r.status_code == 200 and r.json()["update_mode"] == "manual", r.text
            g = c.get("/api/settings/update-mode").json()
            assert g["update_mode"] == "manual", g
    finally:
        app_module._fetch_releases_cached = old_fetch
        app_module.Path = old_path
        app_module._UPDATE_CONFIG_PATH = old_update_cfg
        os.environ.pop("APP_VERSION", None)


# ── Runner (no pytest) ───────────────────────────────────────────────────
def main():
    tests = [
        ("repo layout returns version", test_repo_layout_returns_version),
        ("coherence current == verCurrent == VERSION file", test_coherence_current_matches_vercurrent),
        ("APP_VERSION env wins over repo file", test_app_version_env_wins_over_repo_file),
        ("APP_VERSION '0.0.0' is rejected", test_app_version_zero_zero_is_rejected),
        ("container simulation (cwd!=repo, __file__ elsewhere)", test_container_simulation),
        ("endpoints: version/changes + latest + update + mode", test_endpoints_version_changes_and_update),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"\n{'✅' if failed == 0 else '❌'} VERSION LOAD TESTS: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
