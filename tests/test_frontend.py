"""The front end's own contract (spec §24.1, §25 "no critical buttons are inert").

The UI is plain JS with no build step, so these tests read the source and assert the
things that broke during development. Each one corresponds to a real bug:

* a preset applied but the studio re-rendered over it, so the click looked like a no-op
* the form dialog closed without resolving, so every "New …" button silently did nothing
* the static allowlist did not include the new assets, so the page loaded unstyled
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "app" / "web"
HTML = (WEB / "studio.html").read_text(encoding="utf-8")
JS = (WEB / "studio.js").read_text(encoding="utf-8")
CSS = (WEB / "studio.css").read_text(encoding="utf-8")


class TestAssetsAreServed:
    def test_the_page_references_its_own_assets(self):
        assert 'href="/studio.css"' in HTML
        assert 'src="/studio.js"' in HTML

    def test_every_asset_the_page_needs_is_in_the_allowlist(self):
        from app.web.static_files import PUBLIC_FILES

        for path in ("/", "/studio.html", "/studio.css", "/studio.js"):
            assert path in PUBLIC_FILES, f"{path} would 404"

    def test_the_assets_exist(self):
        for name in ("studio.html", "studio.css", "studio.js"):
            assert (WEB / name).is_file(), name
            assert (WEB / name).stat().st_size > 500, f"{name} looks empty"

    def test_the_old_vite_shell_is_not_what_is_served(self):
        from app.web.static_files import resolve_static

        path, content_type = resolve_static(WEB, "/")
        assert path.name == "studio.html"
        assert content_type.startswith("text/html")


class TestNoInertControls:
    def test_every_nav_item_has_a_view(self):
        views = set(re.findall(r'class="view" id="view-(\w+)"', HTML))
        navs = set(re.findall(r'data-view="(\w+)"', HTML))
        missing = navs - views
        assert not missing, f"nav items with no view: {sorted(missing)}"
        # And nothing is a dead view with no way to reach it.
        assert not (views - navs), f"views with no nav item: {sorted(views - navs)}"

    def test_every_view_has_a_loader(self):
        views = set(re.findall(r'class="view" id="view-(\w+)"', HTML))
        loaders = set(re.findall(r"^\s{2}(\w+): load", JS, re.M))
        assert views == loaders, f"views without a loader: {views - loaders}"

    def test_every_control_the_script_binds_exists_in_the_markup(self):
        """A missing element is a TypeError at boot, which empties the whole page."""
        markup_ids = set(re.findall(r'id="([\w-]+)"', HTML))
        bound = set(re.findall(r"\$\('([\w-]+)'\)\.addEventListener", JS))
        missing = sorted(bound - markup_ids)
        assert not missing, f"script binds ids that are not in the page: {missing}"

    def test_every_dialog_button_is_wired(self):
        """The form dialog once closed without resolving; buttons are explicit now."""
        assert "dlgSave').addEventListener" in JS
        assert "settleDialog(result.values)" in JS
        assert "settleDialog(null)" in JS
        # The implicit `<form method="dialog">` submit is what failed silently.
        assert 'method="dialog"' not in HTML


class TestStudioHonesty:
    def test_parameters_come_from_the_model_not_a_fixed_list(self):
        """The studios build controls from `model.parameters`."""
        assert "model.parameters" in JS
        assert "spec.choices" in JS

    def test_the_offline_demo_is_labelled_in_the_ui(self):
        assert "DEMO IMAGE" in JS
        assert "offline demo" in JS.lower()

    def test_cost_unknown_is_shown_as_unknown(self):
        assert "'unknown'" in JS
        assert "cost unknown" in JS.lower()

    def test_an_empty_state_is_rendered_for_every_list(self):
        assert JS.count("emptyState(") >= 8, "each list view needs an empty state"

    def test_errors_carry_the_server_code(self):
        assert "error.code" in JS or "e.code" in JS
        assert "errorBanner" in JS

    def test_the_ui_calls_no_endpoint_that_does_not_exist(self):
        """Every path the script builds must match a registered route.

        The script composes paths like ``'/api/jobs/' + jobId``, so both sides are
        normalised the same way: each ``{param}`` on a route and each trailing segment
        on a call become ``*``. That makes the comparison structural rather than a
        string match that would fail on every path with an id in it.
        """
        from app.web.routes import router

        def route_key(pattern_text: str) -> str:
            path = re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", r"{\1}", pattern_text[1:-1])
            return re.sub(r"\{[^}]+\}", "*", path)

        registered = {(method.lower(), route_key(p.pattern)) for method, p, _ in router._routes}

        def call_key(raw: str, tail: str = "") -> str:
            """Normalise a call site to a route shape.

            Three shapes occur, and each must keep its structure:

              '/api/jobs'                       exact
              '/api/jobs/' + id                 -> /api/jobs/*
              '/api/jobs/' + id + '/cancel'     -> /api/jobs/*/cancel

            The suffix after an interpolated id matters: without it every nested route
            collapses onto its parent, which makes the check pass on nothing.
            """
            if "?" in raw:                    # a query string is not part of the route
                return raw.split("?")[0].rstrip("/")
            path = raw.rstrip("/")
            if "+" not in tail and "${" not in raw:
                return path
            # Any quoted segment after the first is the rest of the path.
            segments = re.findall(r"'(/[^']*)'", tail)
            return path + "/*" + "".join(segments).rstrip("/")

        called = set()
        # The capture stops at the closing quote, so look at what follows it to see
        # whether the path is continued with `+ id` or is a complete literal.
        for match in re.finditer(r"api\.(get|post|patch|del)\('([^']+)'([^)\n]*)", JS):
            method, raw, tail = match.groups()
            verb = {"get": "get", "post": "post", "patch": "patch", "del": "delete"}[method]
            called.add((verb, call_key(raw, tail.split("\n")[0])))

        unknown = sorted(c for c in called if c not in registered)
        assert not unknown, f"the UI calls routes that do not exist: {unknown}"


class TestResponsiveness:
    def test_there_is_a_narrow_layout(self):
        assert "@media (max-width: 860px)" in CSS

    def test_the_sidebar_collapses_on_small_screens(self):
        narrow = CSS.split("@media (max-width: 860px)")[1]
        assert "--sidebar: 64px" in narrow

    def test_reduced_motion_is_respected(self):
        assert "prefers-reduced-motion" in CSS

    def test_colours_are_variables_not_literals_in_rules(self):
        """A palette defined once is what makes a re-theme mechanical."""
        assert "--accent:" in CSS
        # Count hex literals outside the :root block.
        root_end = CSS.index("}", CSS.index(":root"))
        rest = CSS[root_end:]
        literals = re.findall(r"#[0-9a-fA-F]{3,8}\b", rest)
        assert len(literals) < 12, f"too many hard-coded colours outside :root: {literals[:10]}"
