"""The deployment shape, asserted rather than assumed.

Every claim in the README's deploy section is a test here. These are cheap and they fail
loudly, which matters because a broken `vercel.json` fails at build time on a machine
nobody is watching.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _vercel() -> dict:
    return json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


def _functions() -> list[str]:
    files = []
    for path in (ROOT / "api").rglob("*.py"):
        files.append(path.relative_to(ROOT).as_posix())
    return sorted(files)


def test_the_framework_is_pinned_so_vercel_does_not_guess():
    """A root requirements.txt makes Vercel try to build the repo as one Python app.

    That is what produced PYTHON_ENTRYPOINT_NOT_FOUND on the previous project: it looked
    for a single entrypoint and found nine. `framework: null` is "Other" and stops it.
    """
    assert _vercel().get("framework") is None
    assert "framework" in _vercel()


def test_every_function_is_described_in_vercel_json():
    """A function with no pattern gets the platform defaults, not ours."""
    config = _vercel()
    listed = set(config.get("functions", {}))
    on_disk = set(_functions())
    assert on_disk, "no functions found in api/"
    missing = on_disk - listed
    assert not missing, f"functions with no vercel.json entry: {sorted(missing)}"


def test_every_function_exports_handler():
    """Vercel looks for a module-level `handler`. A typo is a runtime 500, not a build error."""
    for relative in _functions():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert re.search(r"^class handler\b", text, re.MULTILINE), (
            f"{relative} does not export `handler`")


def test_the_api_rewrite_exists_and_the_page_is_reachable():
    rewrites = _vercel().get("rewrites", [])
    sources = [r["source"] for r in rewrites]
    assert "/api/(.*)" in sources, "no rewrite sends /api/* to a function"
    page = [r for r in rewrites if r["destination"] == "/api/page"]
    assert page, "nothing serves the page at /"
    # The page pattern must exclude /api, or it swallows the API.
    assert "api/" in page[0]["source"], "the page pattern would also catch /api/*"


def test_the_cron_points_at_a_route_that_exists():
    crons = _vercel().get("crons", [])
    assert crons, "no cron: queued jobs would never be driven"
    for entry in crons:
        assert entry["path"] == "/api/cron/tick"
        # Hobby accounts reject a schedule that runs more than once a day, and that is
        # the deployment this ships to — so the expression must be a valid five-field
        # cron *and* daily. A finer schedule fails the deploy outright; the inline drive
        # on submit is what makes a daily backstop sufficient.
        fields = entry["schedule"].split()
        assert len(fields) == 5, f"not a five-field cron: {entry['schedule']}"
        assert not re.match(r"^\*$", fields[0]), (
            "an every-minute schedule is rejected on Hobby; jobs are driven inline")
    text = (ROOT / "api/cron/tick.py").read_text(encoding="utf-8")
    assert "/api/cron/tick" in text


def test_source_and_data_are_denied_at_the_edge():
    """`app/` ships because the functions import it; it must not be *downloadable*."""
    routes = _vercel().get("routes", [])
    denied = {r["src"] for r in routes if r.get("status") == 404}
    joined = " ".join(denied)
    for pattern in ("app", "data", "uploads", "tests"):
        assert pattern in joined, f"no 404 rule covering /{pattern}/"
    assert ".env" in joined, "no 404 rule covering env files"


def test_vercelignore_keeps_the_upload_small():
    text = (ROOT / ".vercelignore").read_text(encoding="utf-8")
    for pattern in ("tests/", "scripts/", "docs/", "data/", "uploads/", ".env"):
        assert pattern in text, f".vercelignore does not exclude {pattern}"
    # The env template is documentation and is the one .env file that must ship.
    assert "!.env.example" in text


def test_the_requirements_file_has_what_the_functions_import():
    """The function bundle installs from requirements.txt alone.

    The Postgres driver is the one that bites: `app/db.py` imports it lazily, so a
    deployment with `DATABASE_URL` set but no driver in this file builds cleanly and then
    fails on the first query. That is worse than a build failure, so it is pinned here.
    """
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for needed in ("cryptography", "pillow", "numpy", "psycopg2"):
        assert needed in requirements, f"{needed} missing from requirements.txt"

    # Whatever the driver import is named in db.py must be installed above.
    db_source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    for module in re.findall(r"^\s*import (psycopg\w*)", db_source, re.MULTILINE):
        assert module.replace("_", "-") in requirements or module in requirements, (
            f"db.py imports {module}, which requirements.txt does not install")


def test_the_api_function_prepares_the_schema_before_dispatching():
    """The bug the first deployment shipped: catalog worked, everything else 500'd.

    `/api/catalog` reads no tables, so it answered while every other route hit a schema
    that had never been created — `build_server()` does that locally, and a serverless
    process has no equivalent hook.
    """
    source = (ROOT / "api" / "index.py").read_text(encoding="utf-8")
    assert "def _prepare(" in source
    assert "store.init()" in source

    # It must override `_dispatch`, not the verbs. The base class routes every verb
    # through `_dispatch`; overriding `do_GET` and calling `super().do_GET()` recurses
    # forever, because the base `do_GET` calls `self._dispatch` — the override again.
    # That shipped once as FUNCTION_INVOCATION_FAILED on every request.
    assert "def _dispatch(" in source
    assert "def do_GET(" not in source, (
        "overriding a verb re-enters _dispatch; override _dispatch itself")
    dispatch = source[source.index("def _dispatch"):]
    assert "_prepare()" in dispatch
    assert dispatch.index("_prepare()") < dispatch.index("super()._dispatch")


def test_preparing_twice_is_harmless():
    """`_prepare` runs once per warm instance, but concurrency makes twice possible."""
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("_vc_index", ROOT / "api" / "index.py")
    module = importlib.util.module_from_spec(spec)
    _sys.modules["_vc_index"] = module
    spec.loader.exec_module(module)

    module._prepare()
    first = module._prepared
    module._prepare()
    assert first is True
    assert module._prepared is True
    assert callable(module.handler)


def test_serverless_defaults_point_at_a_writable_directory():
    """A read-only bundle must not become a 500 on every request.

    The first deployment failed exactly here: `DATABASE_URL` was unset, so the app fell
    back to SQLite at `BASE_DIR/data`, and `/var/task` is read-only on Vercel. Every
    route except the two that touch no tables returned `internal_error`. The defaults are
    now chosen from `IS_SERVERLESS`, before anything tries to write.
    """
    import subprocess
    import sys as _sys

    env = {"PATH": __import__("os").environ.get("PATH", ""), "VERCEL": "1",
           "APP_ENCRYPTION_KEY": "deploy-test"}
    probe = (
        "import sys; sys.path.insert(0, r'%s');"
        "from app import config;"
        "print(config.IS_SERVERLESS, config.DATA_DIR.as_posix(), "
        "config.UPLOAD_DIR.as_posix())" % ROOT
    )
    out = subprocess.run([_sys.executable, "-c", probe], capture_output=True,
                         text=True, env=env, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    serverless, data_dir, upload_dir = out.stdout.split()
    assert serverless == "True"
    assert data_dir.startswith("/tmp"), f"DATA_DIR is not writable: {data_dir}"
    assert upload_dir.startswith("/tmp"), f"UPLOAD_DIR is not writable: {upload_dir}"


def test_an_unwritable_directory_is_reported_not_raised():
    """`ensure_dirs` returns failures; it must not throw and take the request with it."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT))
    from app import config

    original_data, original_upload = config.DATA_DIR, config.UPLOAD_DIR
    try:
        # A path under a file cannot be created as a directory.
        config.DATA_DIR = pathlib.Path(ROOT / "vercel.json" / "nope")
        config.UPLOAD_DIR = original_upload
        failures = config.ensure_dirs()
        assert isinstance(failures, list)
        assert failures, "an impossible directory should be reported"
    finally:
        config.DATA_DIR, config.UPLOAD_DIR = original_data, original_upload


def test_the_ui_reports_a_degraded_deployment():
    """An empty list on a misconfigured deployment must not look like a broken app.

    With no durable database every instance has its own data, so a job created a second
    ago can be absent on the next request. That is a configuration state, and the UI has
    to say so rather than rendering "No jobs yet" as though nothing ever happened.
    """
    script = (ROOT / "app" / "web" / "studio.js").read_text(encoding="utf-8")
    assert "health.status === 'degraded'" in script
    assert "degraded — data is not durable" in script
    assert "health.problems" in script


def test_the_page_strips_a_query_string():
    """`/?cb=1` returned 404 for the homepage.

    The rewrite hands the whole request target to the page function, and the static
    resolver looks for a file with that exact name — so `?cb=1` matched nothing. A query
    string is never part of a path.
    """
    source = (ROOT / "api" / "page.py").read_text(encoding="utf-8")
    assert 'split("?", 1)[0]' in source
    assert 'split("#", 1)[0]' in source
