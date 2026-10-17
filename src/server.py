#!/usr/bin/env python3
"""
img-gen server — full backend with projects, presets, assets, chat, jobs, gallery.

Architecture (layered, provider-agnostic):

    HTTP handler (routing + validation)
        -> JobService / ChatService / PresetService (business logic)
            -> Provider adapters (OpenAI-compatible; no provider is hard-coded)
                -> persistence via db.py (SQLite)

Providers and models are never hard-coded. Every upstream call is described
entirely by data stored in a preset (url, key, model), so any OpenAI-compatible
endpoint (OpenAI, Azure, Ollama, LocalAI, vLLM, Together, Groq, ...) works.
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
import base64
import hashlib
import http.client
import io
import ssl
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from http.server import HTTPServer

from db import get_db, init_db, dict_from_row, dicts_from_rows

PROJECT_DIR = Path(__file__).resolve().parent.parent
PORT = int(os.getenv("IMGGEN_PORT", "8765"))
HOST = os.getenv("IMGGEN_HOST", "127.0.0.1")
MAX_BODY = 25 * 1024 * 1024
UPSTREAM_TIMEOUT = int(os.getenv("IMGGEN_TIMEOUT", "180"))

# Assets/images are persisted in SQLite, but we also keep a file cache dir so
# large blobs can be served without repeated b64 decoding.
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("img-gen")

init_db()

MAX_JOB_ATTEMPTS = 3
JOB_POLL_INTERVAL = 1.0
_JOB_WORKER_STARTED = False
_JOB_LOCK = threading.Lock()
_CANCELLED_JOBS = set()


# ==========================================================================
# Security helpers
# ==========================================================================

def mask_key(key):
    """Never return a usable secret to the client."""
    if not key:
        return ""
    key = str(key)
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


_BLOCKED_HOSTS = {"metadata.google.internal", "instance-data"}

# SSRF guard blocks loopback/private ranges by default. Local testing against a
# mock provider requires an explicit opt-in; never enable in production.
ALLOW_PRIVATE_UPSTREAM = os.getenv("IMGGEN_ALLOW_PRIVATE_UPSTREAM", "").lower() in ("1", "true", "yes")


def validate_upstream_url(url):
    """Reject malformed or obviously unsafe upstream URLs.

    Mitigates SSRF by requiring an absolute http(s) URL and blocking
    loopback/link-local/metadata targets, plus plaintext credentials in the URL.
    """
    if not url:
        return False, "api_url is required"
    url = str(url).strip()
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "invalid api_url"

    if parsed.scheme not in ("http", "https"):
        return False, "api_url must use http or https"

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "api_url must include a host"

    if parsed.username or parsed.password:
        return False, "api_url must not embed credentials"

    if hostname in _BLOCKED_HOSTS or hostname.endswith(".internal"):
        return False, "api_url host is not allowed"

    if not ALLOW_PRIVATE_UPSTREAM:
        # Resolve the hostname and check the actual IP — string checks miss
        # alternate encodings (http://2130706433/, http://0x7f000001/, 127.1).
        import socket
        import ipaddress

        try:
            infos = socket.getaddrinfo(hostname, None)
        except Exception:
            return False, "api_url host could not be resolved"
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if (ip.is_loopback or ip.is_link_local or ip.is_private
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False, f"api_url resolves to a non-public address ({ip})"

    return True, None


def resolve_validated_ip(url):
    """Resolve the URL's hostname to a public IP for connection pinning.

    Call only after validate_upstream_url() passed. Returns (ip, None) or
    (None, error). Pinning the connection to the validated IP closes the
    DNS-rebinding window (validate -> connect race).
    """
    import socket
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        hostname = parsed.hostname
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except Exception as e:
        return None, f"could not resolve host: {e}"
    for info in infos:
        ip = info[4][0]
        return ip, None
    return None, "no address returned"


# ==========================================================================
# Provider adapters — data-driven, no hard-coded providers or models
# ==========================================================================

_ADAPTERS = {}
_ADAPTER_LOCK = threading.Lock()


def register_adapter(adapter):
    """Register a ProviderAdapter by its `name` so presets with a matching
    `*_provider` value use it. Extension point for non-OpenAI dialects:

        class MyDialect(ProviderAdapter):
            name = "my-dialect"
            def generate(self, cfg, prompt, params): ...
            def chat(self, cfg, messages, **kw): ...

        register_adapter(MyDialect())

    get_adapter() falls back to the OpenAI-compatible dialect for unknown
    providers, so nothing is required unless behavior differs.
    """
    if not isinstance(adapter, ProviderAdapter):
        raise TypeError("adapter must subclass ProviderAdapter")
    with _ADAPTER_LOCK:
        _ADAPTERS[adapter.name] = adapter


def get_adapter(provider=""):
    """Adapter lookup. Unknown providers fall back to the OpenAI-compatible
    dialect, which keeps the system extensible without code changes."""
    with _ADAPTER_LOCK:
        adapter = _ADAPTERS.get((provider or "").strip().lower())
    return adapter if adapter else OpenAICompatibleAdapter()

class ProviderAdapter:
    """Base adapter. Subclasses translate a normalized request into a
    provider-specific HTTP call. Selection is by preset data, never by
    hard-coded provider names in business logic."""

    name = "generic"

    # Offline adapters do no network I/O, so URL validation and IP pinning
    # are skipped for them (see run_job / ChatService.run).
    offline = False

    def generate(self, cfg, prompt, params):
        raise NotImplementedError

    def chat(self, cfg, messages, **kw):
        raise NotImplementedError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Block silent redirects: the SSRF guard runs on the original URL only,
    and urllib would otherwise forward the Authorization header to whatever
    host a 30x points at."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_UPSTREAM_OPENER = urllib.request.build_opener(_NoRedirect)

# Default TLS context for pinned connections (cert verification stays on).
_SSL_CTX = ssl.create_default_context()


def _host_for_conn(ip):
    """Bracket IPv6 literals so http.client's host:port parsing works."""
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]"
    return ip


def _split_host_port(hostport):
    """Split 'host' or 'host:port' (urllib passes req.host, which may carry
    the port) into (host, port-or-None) so pinned connections keep the port."""
    if hostport.startswith("["):  # [v6]:port
        host, _, rest = hostport[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else None
        return host, port
    host, sep, port = hostport.rpartition(":")
    if sep and port.isdigit():
        return host, int(port)
    return hostport, None


class _PinnedHTTPSConn(http.client.HTTPSConnection):
    """HTTPSConnection that dials a pinned IP but performs the TLS handshake
    with the real hostname (SNI + cert verification). Needed because
    HTTPSConnection.__init__ has no server_hostname kwarg and connect()
    derives SNI from self.host, which here is the pinned IP."""

    def __init__(self, sni_host, ip, **kw):
        self._sni_host = sni_host
        kw.setdefault("context", _SSL_CTX)
        super().__init__(ip, **kw)

    def connect(self):
        http.client.HTTPConnection.connect(self)  # plain TCP to the pinned IP
        server_hostname = self._tunnel_host or self._sni_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedHTTPSConnection(urllib.request.HTTPSHandler):
    """HTTPS connection that always dials a pre-validated IP while keeping
    the original hostname for TLS SNI/cert verification. Closes the
    validate-then-connect (DNS rebinding) window."""

    def __init__(self, ip):
        super().__init__(context=_SSL_CTX)
        self._ip = ip

    def https_open(self, req):
        return self.do_open(self._conn_factory, req)

    def _conn_factory(self, host, **kw):
        kw.pop("server_hostname", None)  # newer urllib may forward it; HTTPSConnection rejects it
        sni_host, port = _split_host_port(host)
        if port:
            kw["port"] = port
        return _PinnedHTTPSConn(sni_host, _host_for_conn(self._ip), **kw)


class _PinnedHTTPConnection(urllib.request.HTTPHandler):
    def __init__(self, ip):
        super().__init__()
        self._ip = ip

    def http_open(self, req):
        return self.do_open(self._http_factory, req)

    def _http_factory(self, host, **kw):
        _, port = _split_host_port(host)
        if port:
            kw["port"] = port
        return http.client.HTTPConnection(_host_for_conn(self._ip), **kw)


class _HostHeaderRewriter(urllib.request.BaseHandler):
    """urllib drops the Host header when the URL host is replaced; re-add the
    original host so pinned connections still address the right vhost."""

    def http_request(self, req):
        original_host = urllib.parse.urlparse(req.full_url).hostname
        if original_host:
            req.add_unredirected_header("Host", original_host)
        return req

    https_request = http_request


def _build_pinned_opener(ip, scheme):
    if scheme == "https":
        return urllib.request.build_opener(_NoRedirect, _PinnedHTTPSConnection(ip))
    return urllib.request.build_opener(_NoRedirect, _PinnedHTTPConnection(ip), _HostHeaderRewriter())


class OpenAICompatibleAdapter(ProviderAdapter):
    """Speaks the OpenAI REST dialect. Works for OpenAI plus any compatible
    gateway (Ollama, LocalAI, vLLM, Together, Groq, OpenRouter, ...)."""

    name = "openai-compatible"

    @staticmethod
    def _post(url, key, payload, timeout=UPSTREAM_TIMEOUT, pinned_ip=None):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "img-gen/4.0",
            },
        )
        t0 = time.time()
        try:
            if pinned_ip and not ALLOW_PRIVATE_UPSTREAM:
                scheme = urllib.parse.urlparse(url).scheme
                # Rewrite the URL to dial the validated IP; Host header keeps
                # the original hostname so TLS/vhost routing still works.
                p = urllib.parse.urlparse(url)
                pinned_url = urllib.parse.urlunparse(p._replace(netloc=pinned_ip if p.port in (None, {"http": 80, "https": 443}[scheme]) else f"{pinned_ip}:{p.port}"))
                req = urllib.request.Request(
                    pinned_url,
                    data=req.data, method=req.get_method(),
                    headers={**dict(req.header_items()), "Host": p.hostname},
                )
                opener = _build_pinned_opener(pinned_ip, scheme)
                with opener.open(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode())
                    log.info("upstream ok (pinned %s)  %.1fs  %s", pinned_ip, time.time() - t0, url[:80])
                    return data, None
            with _UPSTREAM_OPENER.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                log.info("upstream ok  %.1fs  %s", time.time() - t0, url[:80])
                return data, None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:500]
            log.error("upstream %s  %s", e.code, detail)
            return None, f"upstream {e.code}: {detail}"
        except Exception as e:
            log.exception("upstream fail")
            return None, str(e)

    def generate(self, cfg, prompt, params):
        url = (cfg.get("url") or "").rstrip("/")
        if not url.endswith("/images/generations"):
            url += "/images/generations"
        payload = {
            "model": cfg.get("model"),
            "prompt": prompt,
            "n": params.get("n", 1),
            "size": params.get("size", "1024x1024"),
        }
        for key in ("quality", "style", "negative_prompt", "image", "seed", "response_format"):
            if params.get(key) not in (None, ""):
                payload[key] = params[key]
        data, err = self._post(url, cfg.get("key", ""), payload, pinned_ip=cfg.get("pinned_ip"))
        if err:
            return [], err
        out = []
        for item in data.get("data", []):
            out.append({"b64_json": item.get("b64_json", ""), "url": item.get("url", "")})
        return out, None

    def chat(self, cfg, messages, temperature=0.7, max_tokens=4096, response_format=None):
        url = (cfg.get("url") or "").rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {
            "model": cfg.get("model"),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, 16384),
        }
        if response_format:
            payload["response_format"] = response_format
        return self._post(url, cfg.get("key", ""), payload, pinned_ip=cfg.get("pinned_ip"))


# ==========================================================================
# Offline demo provider — no external services required
# ==========================================================================
#
# Lets the studio run with nothing else installed: images are rendered locally
# with PIL as deterministic 1-bit halftone art seeded by the prompt, and the
# "logic model" is a small rule-based planner that emits the same JSON plan
# schema a real model would. Presets select it with provider "demo-image" /
# "demo-logic"; both are marked offline so URL validation is skipped.

_BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)


def _seed_from(text):
    return int(hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12], 16)


def _parse_size(size, default=(1024, 1024)):
    try:
        w, h = str(size).lower().split("x")
        w, h = int(w), int(h)
        if 64 <= w <= 4096 and 64 <= h <= 4096:
            return w, h
    except Exception:
        pass
    return default


def render_demo_image(prompt, size="1024x1024", seed=None):
    """Deterministic halftone artwork derived from the prompt.

    Returns PNG bytes. The same prompt and seed always produce the same image,
    so a seeded gallery stays stable across restarts.
    """
    import numpy as np
    from PIL import Image

    w, h = _parse_size(size)
    s = int(seed) if seed not in (None, "") else _seed_from(prompt)
    rng = np.random.default_rng(s & 0xFFFFFFFF)

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = xs / max(w - 1, 1), ys / max(h - 1, 1)

    # Base gradient along a seed-chosen angle.
    ang = rng.uniform(0, 2 * np.pi)
    field = nx * np.cos(ang) + ny * np.sin(ang)
    field = (field - field.min()) / max(float(np.ptp(field)), 1e-6)

    # Soft blobs form the "subject" of the composition.
    for _ in range(int(rng.integers(2, 5))):
        cx, cy = rng.uniform(0.1, 0.9), rng.uniform(0.1, 0.9)
        r = rng.uniform(0.15, 0.45)
        amp = rng.uniform(0.4, 1.1) * float(rng.choice([-1.0, 1.0]))
        d2 = (nx - cx) ** 2 + (ny - cy) ** 2
        field = field + amp * np.exp(-d2 / (2 * r * r))

    # Vignette plus fine grain, so the dither has tonal range to work with.
    vx, vy = (nx - 0.5) * 2, (ny - 0.5) * 2
    field = field - 0.35 * np.clip(np.sqrt(vx * vx + vy * vy) - 0.35, 0, None)
    field = field + rng.normal(0, 0.02, size=(h, w)).astype(np.float32)

    lo, hi = np.percentile(field, [2, 98])
    field = np.clip((field - lo) / max(float(hi - lo), 1e-6), 0, 1)

    # Ordered (Bayer) dithering -> 1-bit, matching the studio's Y2K look.
    bayer = np.array(_BAYER8, dtype=np.float32) / 64.0
    thresh = np.tile(bayer, (h // 8 + 1, w // 8 + 1))[:h, :w]
    bits = (field > thresh).astype(np.uint8) * 255

    img = Image.fromarray(bits, mode="L").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


class DemoImageAdapter(ProviderAdapter):
    """Offline image provider: renders halftone art locally, no network."""

    name = "demo-image"
    offline = True

    def generate(self, cfg, prompt, params):
        try:
            n = max(1, min(4, int(params.get("n", 1))))
        except Exception:
            n = 1
        size = params.get("size") or "1024x1024"
        base_seed = params.get("seed")
        out = []
        for i in range(n):
            seed = None
            if base_seed not in (None, ""):
                try:
                    seed = int(base_seed) + i
                except Exception:
                    seed = None
            raw = render_demo_image(prompt, size, seed)
            out.append({"b64_json": base64.b64encode(raw).decode("ascii"), "url": ""})
        return out, None


class DemoLogicAdapter(ProviderAdapter):
    """Offline 'logic model': rule-based planner emitting the plan JSON schema."""

    name = "demo-logic"
    offline = True

    _SMALLTALK = {"hi", "hello", "hey", "thanks", "thank you", "who are you", "what can you do"}

    _RATIO_HINTS = (
        ("16:9", "16:9"), ("wide", "16:9"), ("landscape", "16:9"), ("banner", "16:9"),
        ("9:16", "9:16"), ("tall", "9:16"), ("portrait", "9:16"), ("vertical", "9:16"),
        ("4:3", "4:3"), ("3:4", "3:4"), ("square", "1:1"),
    )

    def chat(self, cfg, messages, temperature=0.7, max_tokens=4096, response_format=None):
        user = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                user = (m.get("content") or "").strip()
                break
        return {"choices": [{"message": {"content": json.dumps(self._plan(user))}}]}, None

    def _plan(self, text):
        low = text.lower().strip(" .!?")
        if not low or low in self._SMALLTALK:
            return {"action": "chat", "message":
                    "I'm the offline demo planner — no API key needed. Describe an image "
                    "and I'll queue it for generation."}

        ratio = "1:1"
        for hint, val in self._RATIO_HINTS:
            if hint in low:
                ratio = val
                break

        count = 1
        m = (re.search(r"\b(\d+)\s*(?:images?|variations?|versions?|copies)\b", low)
             or re.search(r"\bx\s*(\d)\b", low))
        if m:
            count = max(1, min(4, int(m.group(1))))

        return {"action": "generate", "generations":
                [{"prompt": text.strip()[:4000], "aspectRatio": ratio, "count": count}]}


register_adapter(DemoImageAdapter())
register_adapter(DemoLogicAdapter())


def image_cfg(preset):
    return {"url": preset["image_url"], "key": preset["image_key"], "model": preset["image_model"],
            "provider": preset["image_provider"]}

def logic_cfg(preset):
    return {"url": preset["logic_url"], "key": preset["logic_key"], "model": preset["logic_model"],
            "provider": preset["logic_provider"]}


# ==========================================================================
# Structured generation plan (JSON) — validated, not trusted
# ==========================================================================

ALLOWED_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16")
RATIO_SIZES = {
    "1:1": "1024x1024",
    "3:4": "864x1184",
    "4:3": "1184x864",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
}
MAX_PLAN_ITEMS = 8


def validate_plan(obj):
    """Coerce an arbitrary model response into a trusted generation plan.

    Returns (plan, error). Never raises. Unsafe fields are dropped, not trusted.
    """
    if not isinstance(obj, dict):
        return None, "plan is not an object"
    action = obj.get("action")
    if action not in ("generate", "chat"):
        return None, "unknown action"

    if action == "chat":
        msg = obj.get("message")
        if not isinstance(msg, str) or not msg.strip():
            return None, "chat action requires a message"
        return {"action": "chat", "message": msg.strip()[:4000]}, None

    gens = obj.get("generations")
    if not isinstance(gens, list) or not gens:
        return None, "generate action requires a non-empty generations list"

    items = []
    for g in gens[:MAX_PLAN_ITEMS]:
        if not isinstance(g, dict):
            continue
        prompt = g.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        ratio = g.get("aspectRatio") or g.get("aspect_ratio") or "1:1"
        if ratio not in ALLOWED_RATIOS:
            ratio = "1:1"
        try:
            count = int(g.get("count", 1))
        except Exception:
            count = 1
        count = max(1, min(4, count))
        items.append({
            "prompt": prompt.strip()[:4000],
            "aspectRatio": ratio,
            "count": count,
            "negative_prompt": str(g.get("negativePrompt") or "")[:1000],
        })
    if not items:
        return None, "no usable generations in plan"
    return {"action": "generate", "generations": items}, None


# ==========================================================================
# Job execution
# ==========================================================================

def run_job(jid):
    """Execute a queued generation job against its preset's image provider."""
    db = get_db()
    t0 = time.time()
    db.execute("UPDATE generation_jobs SET status='running', error='', updated_at=datetime('now') WHERE id=?", (jid,))
    db.commit()

    job = db.execute("SELECT * FROM generation_jobs WHERE id=?", (jid,)).fetchone()
    if not job:
        return
    job = dict(job)

    preset = None
    if job.get("preset_id"):
        preset = db.execute("SELECT * FROM presets WHERE id=?", (job["preset_id"],)).fetchone()
        preset = dict(preset) if preset else None

    if not preset or not preset.get("image_model"):
        db.execute("UPDATE generation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                   ("no image model configured for this project's preset", jid))
        db.commit()
        return

    adapter = get_adapter(preset.get("image_provider"))

    if not adapter.offline and not preset.get("image_url"):
        db.execute("UPDATE generation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                   ("no image api_url configured for this project's preset", jid))
        db.commit()
        return

    if not adapter.offline:
        ok, url_err = validate_upstream_url(preset["image_url"])
        if not ok:
            db.execute("UPDATE generation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                       (url_err, jid))
            db.commit()
            return

    if jid in _CANCELLED_JOBS:
        _CANCELLED_JOBS.discard(jid)
        db.execute("UPDATE generation_jobs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (jid,))
        db.commit()
        return

    try:
        params = json.loads(job.get("parameters_json") or "{}")
    except Exception:
        params = {}
    if not isinstance(params, dict):
        params = {}

    ratio = params.get("aspectRatio") or params.get("aspect_ratio") or "1:1"
    if ratio not in ALLOWED_RATIOS:
        ratio = "1:1"
    size = params.get("size") or RATIO_SIZES[ratio]
    try:
        n = max(1, min(4, int(params.get("count", 1))))
    except Exception:
        n = 1

    draft = {
        "n": n,
        "size": size,
        "negative_prompt": params.get("negative_prompt", ""),
    }
    if params.get("seed") not in (None, ""):
        try:
            draft["seed"] = int(params["seed"])
        except Exception:
            pass
    for k in ("quality", "style"):
        if params.get(k):
            draft[k] = params[k]

    adapter = get_adapter(preset.get("image_provider"))
    cfg = image_cfg(preset)
    if not adapter.offline and not ALLOW_PRIVATE_UPSTREAM:
        cfg["pinned_ip"], ip_err = resolve_validated_ip(cfg["url"])
        if ip_err:
            db.execute("UPDATE generation_jobs SET status='failed', error=?, updated_at=datetime('now') WHERE id=?",
                       (ip_err, jid))
            db.commit()
            return
    images, err = adapter.generate(cfg, job["prompt"], draft)

    if err:
        attempt = int(job.get("attempts") or 0) + 1
        if attempt < MAX_JOB_ATTEMPTS:
            db.execute("UPDATE generation_jobs SET status='queued', attempts=?, error=?, updated_at=datetime('now') WHERE id=?",
                       (attempt, err, jid))
        else:
            db.execute("UPDATE generation_jobs SET status='failed', attempts=?, error=?, updated_at=datetime('now') WHERE id=?",
                       (attempt, err, jid))
        db.commit()
        return

    for img in images:
        iid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO generated_images (id, job_id, project_id, conversation_id, preset_id, prompt, "
            "image_data, image_url, parameters_json, logic_model, image_model, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, jid, job["project_id"], job.get("conversation_id", ""), job.get("preset_id", ""),
             job["prompt"], img.get("b64_json", ""), img.get("url", ""), json.dumps(draft),
             preset.get("logic_model", ""), preset.get("image_model", ""), "completed"),
        )
    db.execute("UPDATE generation_jobs SET status='completed', attempts=?, result_json=?, error='', updated_at=datetime('now') WHERE id=?",
               (int(job.get("attempts") or 0) + 1, json.dumps({"count": len(images)}), jid))
    db.commit()
    log.info("job %s completed (%d images, %.1fs)", jid[:8], len(images), time.time() - t0)


def recover_stale_jobs():
    """Crash recovery: jobs left 'running' by a previous process can never
    finish — requeue them (respecting the retry ceiling) so the worker
    drains them on startup."""
    db = get_db()
    rows = db.execute("SELECT id, attempts FROM generation_jobs WHERE status='running'").fetchall()
    for r in rows:
        if int(r["attempts"] or 0) >= MAX_JOB_ATTEMPTS:
            db.execute(
                "UPDATE generation_jobs SET status='failed', error='interrupted by server restart', "
                "updated_at=datetime('now') WHERE id=?", (r["id"],))
        else:
            db.execute(
                "UPDATE generation_jobs SET status='queued', error='recovered after restart', "
                "updated_at=datetime('now') WHERE id=?", (r["id"],))
        log.info("recovered stale running job %s", r["id"][:8])
    if rows:
        db.commit()


def job_worker():
    """Background worker: drains queued jobs one at a time, honours cancels."""
    while True:
        try:
            db = get_db()
            row = db.execute(
                "SELECT id FROM generation_jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                time.sleep(JOB_POLL_INTERVAL)
                continue
            jid = row["id"]
            if jid in _CANCELLED_JOBS:
                _CANCELLED_JOBS.discard(jid)
                db.execute("UPDATE generation_jobs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (jid,))
                db.commit()
                continue
            run_job(jid)
        except Exception:
            log.exception("job worker crashed; restarting loop")
            time.sleep(JOB_POLL_INTERVAL)


def start_job_worker():
    global _JOB_WORKER_STARTED
    with _JOB_LOCK:
        if _JOB_WORKER_STARTED:
            return
        _JOB_WORKER_STARTED = True
    recover_stale_jobs()
    t = threading.Thread(target=job_worker, daemon=True, name="job-worker")
    t.start()
    log.info("job worker started")


# ==========================================================================
# Services
# ==========================================================================

class ChatService:
    SYSTEM_BASE = (
        "You are a creative AI image generation assistant inside an image studio app. "
        "You help users refine ideas and decide when images should be generated."
    )

    SCHEMA = (
        "\n\nRespond with a single JSON object and nothing else.\n"
        'If the user wants images generated: {"action":"generate","generations":'
        '[{"prompt":"...","aspectRatio":"1:1","count":1}]}\n'
        'Allowed aspectRatio values: "1:1","3:4","4:3","16:9","9:16". count is 1-4. '
        "You may include up to 8 generations.\n"
        'Otherwise reply: {"action":"chat","message":"..."}\n'
    )

    def build_messages(self, db, conv, preset, cid):
        msgs = [{"role": "system", "content": (preset["system_prompt"] or self.SYSTEM_BASE)}]

        project = db.execute("SELECT * FROM projects WHERE id=?", (conv["project_id"],)).fetchone()
        if project and (project["instructions"] or "").strip():
            msgs.append({"role": "system", "content": "Project instructions:\n" + project["instructions"].strip()})

        assets = db.execute(
            "SELECT id, name, file_type, description FROM assets WHERE project_id=? ORDER BY created_at DESC",
            (conv["project_id"],),
        ).fetchall()
        if assets:
            info = "\n".join(f"- {a['name']} ({a['file_type']}): {a['description'] or 'no description'}" for a in assets)
            msgs.append({"role": "system", "content": "Available project assets (usable as reference):\n" + info})

        prev = db.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY created_at ASC, rowid ASC", (cid,)
        ).fetchall()
        for m in prev:
            msgs.append({"role": m["role"], "content": m["content"]})

        msgs.append({"role": "system", "content": self.SCHEMA})
        return msgs

    def run(self, cid, content):
        db = get_db()
        conv = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not conv:
            return None, ("conversation not found", 404)
        conv = dict(conv)

        uid = str(uuid.uuid4())
        db.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
                   (uid, cid, "user", content))
        db.execute("UPDATE conversations SET updated_at=datetime('now') WHERE id=?", (cid,))
        db.commit()

        project = db.execute("SELECT * FROM projects WHERE id=?", (conv["project_id"],)).fetchone()
        preset = None
        if project and project["active_preset_id"]:
            preset = db.execute("SELECT * FROM presets WHERE id=?", (project["active_preset_id"],)).fetchone()
            preset = dict(preset) if preset else None

        assistant_id = str(uuid.uuid4())
        jobs = []

        if preset and preset["logic_model"] and (
                get_adapter(preset["logic_provider"]).offline
                or (preset["logic_url"] and preset["logic_key"])):
            adapter = get_adapter(preset["logic_provider"])
            lcfg = logic_cfg(preset)
            if adapter.offline:
                ai_content = None
                data, err = adapter.chat(lcfg, self.build_messages(db, conv, preset, cid),
                                         response_format={"type": "json_object"})
                if err:
                    ai_content = f"[Error: {err}]"
                else:
                    try:
                        ai_content = data["choices"][0]["message"]["content"]
                    except Exception:
                        ai_content = "[Error: unexpected logic model response shape]"
            else:
                ok, url_err = validate_upstream_url(preset["logic_url"])
                if not ok:
                    ai_content = f"[Error: invalid logic api_url — {url_err}]"
                else:
                    messages = self.build_messages(db, conv, preset, cid)
                    if not ALLOW_PRIVATE_UPSTREAM:
                        lcfg["pinned_ip"], ip_err = resolve_validated_ip(lcfg["url"])
                        if ip_err:
                            ai_content = f"[Error: {ip_err}]"
                            lcfg = None
                    if lcfg is not None:
                        data, err = adapter.chat(lcfg, messages, response_format={"type": "json_object"})
                        if err:
                            ai_content = f"[Error: {err}]"
                        else:
                            try:
                                ai_content = data["choices"][0]["message"]["content"]
                            except Exception:
                                ai_content = "[Error: unexpected logic model response shape]"
        else:
            ai_content = "[No logic model configured. Create a preset with a logic model and assign it to this project.]"

        db.execute("INSERT INTO messages (id, conversation_id, role, content) VALUES (?,?,?,?)",
                   (assistant_id, cid, "assistant", ai_content))
        db.commit()

        try:
            plan, perr = validate_plan(json.loads(ai_content))
        except Exception:
            plan, perr = None, "model did not return valid JSON"

        if plan and plan["action"] == "generate":
            for gen in plan["generations"]:
                jid = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO generation_jobs (id, project_id, conversation_id, message_id, preset_id, prompt, "
                    "negative_prompt, parameters_json, status, logic_model, image_model) "
                    "VALUES (?,?,?,?,?,?,?,?,'queued',?,?)",
                    (jid, conv["project_id"], cid, assistant_id, preset["id"] if preset else "",
                     gen["prompt"], gen.get("negative_prompt", ""), json.dumps(gen),
                     preset["logic_model"] if preset else "", preset["image_model"] if preset else ""),
                )
                jobs.append(jid)
            db.commit()
            log.info("queued %d job(s) for conversation %s", len(jobs), cid[:8])
        elif perr:
            log.warning("plan rejected: %s", perr)

        user_msg = dict_from_row(db.execute("SELECT * FROM messages WHERE id=?", (uid,)).fetchone())
        asst_msg = dict_from_row(db.execute("SELECT * FROM messages WHERE id=?", (assistant_id,)).fetchone())
        result = {"user_message": user_msg, "assistant_message": asst_msg,
                  "jobs": jobs, "plan": plan, "plan_error": perr}
        return result, None


# ==========================================================================
# HTTP
# ==========================================================================

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def sanitize_static_path(rel):
    """Allow-list static file serving: only index.html + top-level assets.

    Normalize BEFORE checking (a `//data/...` prefix would otherwise bypass
    block-lists via lstrip), then deny sensitive dirs, then require a known
    static extension so the SQLite DB / backups can never be served.
    """
    rel = (rel or "/").split("?")[0].split("#")[0]
    if rel in ("/", ""):
        rel = "/ui.html"
    rel = rel.lstrip("/")
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("..", ".") for p in parts) or not parts:
        return None
    first = parts[0]
    if first in ("data", "__pycache__", ".opencode", "node_modules", ".git"):
        return None
    if first == "src" and rel == "src/app.js":
        return rel  # the vanilla UI's script; anything else under src/ stays blocked
    if first == "src":
        return None
    if first.startswith("."):
        return None
    if Path(rel).suffix.lower() not in STATIC_TYPES:
        return None  # e.g. img-gen.db, server.py.bak, package-lock.json
    return rel


class Handler(BaseHTTPRequestHandler):
    server_version = "img-gen/4.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # ---- plumbing -------------------------------------------------------
    def _cors_origin(self):
        """Restrict CORS to the dev frontend origin. Requests via the Vite
        proxy are same-origin and need no CORS header at all."""
        origin = self.headers.get("Origin", "")
        if origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
            return origin
        return None

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=400):
        log.warning("error %s: %s", status, msg)
        self._json({"error": msg}, status)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except Exception:
            return None, "invalid Content-Length"
        if length < 0:
            return None, "invalid Content-Length"
        if length > MAX_BODY:
            return None, "request too large"
        try:
            return json.loads(self.rfile.read(length) or b"{}"), None
        except Exception as e:
            return None, f"invalid json: {e}"

    def _preset_out(self, row, include_secrets=False):
        p = dict_from_row(row)
        if p is None:
            return None
        if not include_secrets:
            p["logic_key"] = mask_key(p.get("logic_key"))
            p["image_key"] = mask_key(p.get("image_key"))
        return p

    # ---- verbs ----------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = (self.path or "").rstrip("/") or "/"
        if not path.startswith("/api/"):
            return self._serve_static(path)

        db = get_db()

        if path == "/api/health":
            return self._json({"ok": True, "uptime": time.time()})

        if path == "/api/projects":
            rows = db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
            out = []
            for r in rows:
                p = dict(r)
                p["asset_count"] = db.execute("SELECT COUNT(*) FROM assets WHERE project_id=?", (p["id"],)).fetchone()[0]
                p["generation_count"] = db.execute("SELECT COUNT(*) FROM generated_images WHERE project_id=?", (p["id"],)).fetchone()[0]
                out.append(p)
            return self._json(out)

        # Must be checked before /api/projects/<id>: a sub-resource path such as
        # /api/projects/<id>/assets would otherwise be parsed as a project id.
        if re.match(r"^/api/projects/[^/]+/assets$", path):
            pid = path.split("/")[3]
            rows = db.execute(
                "SELECT id, project_id, name, file_type, size, description, tags, created_at "
                "FROM assets WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()
            return self._json(dicts_from_rows(rows))

        if path.startswith("/api/projects/"):
            pid = path.split("/")[3]
            row = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                return self._error("project not found", 404)
            p = dict(row)
            p["asset_count"] = db.execute("SELECT COUNT(*) FROM assets WHERE project_id=?", (pid,)).fetchone()[0]
            p["generation_count"] = db.execute("SELECT COUNT(*) FROM generated_images WHERE project_id=?", (pid,)).fetchone()[0]
            return self._json(p)

        if path == "/api/presets":
            rows = db.execute("SELECT * FROM presets ORDER BY name").fetchall()
            return self._json([self._preset_out(r) for r in rows])

        if path.startswith("/api/presets/"):
            prid = path.split("/")[3]
            row = db.execute("SELECT * FROM presets WHERE id=?", (prid,)).fetchone()
            if not row:
                return self._error("preset not found", 404)
            return self._json(self._preset_out(row))

        if path.startswith("/api/assets/"):
            pid = path.split("/")[3]
            rows = db.execute(
                "SELECT id, project_id, name, file_type, size, description, tags, created_at "
                "FROM assets WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()
            return self._json(dicts_from_rows(rows))

        if path.startswith("/api/asset-file/"):
            aid = path.split("/")[3]
            row = db.execute("SELECT data, file_type FROM assets WHERE id=?", (aid,)).fetchone()
            if not row or not row["data"]:
                return self._error("asset not found", 404)
            ct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "webp": "image/webp"}.get(row["file_type"], "application/octet-stream")
            if row["file_type"] == "svg":
                # SVG can carry scripts; serve it as a download, not as
                # same-origin executable content.
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=\"asset.svg\"")
                self.send_header("Content-Length", str(len(row["data"])))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(row["data"])
                return
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(row["data"])))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(row["data"])
            return

        if path.startswith("/api/conversations/"):
            pid = path.split("/")[3]
            rows = db.execute("SELECT * FROM conversations WHERE project_id=? ORDER BY updated_at DESC", (pid,)).fetchall()
            return self._json(dicts_from_rows(rows))

        if path.startswith("/api/messages/"):
            cid = path.split("/")[3]
            rows = db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC, rowid ASC", (cid,)).fetchall()
            return self._json(dicts_from_rows(rows))

        if path.startswith("/api/jobs/"):
            pid = path.split("/")[3]
            rows = db.execute("SELECT * FROM generation_jobs WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()
            return self._json(dicts_from_rows(rows))

        if path.startswith("/api/gallery/"):
            pid = path.split("/")[3]
            rows = db.execute("SELECT * FROM generated_images WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()
            out = []
            for r in rows:
                img = dict(r)
                has_b64 = bool(img.get("image_data"))
                img["image_data"] = ""  # bytes are heavy; served via /api/gallery-image/<id>
                if has_b64:
                    # Always give the client a loadable src, even when the
                    # provider returned inline b64 instead of a hosted URL.
                    img["image_url"] = "/api/gallery-image/" + img["id"]
                out.append(img)
            return self._json(out)

        if path.startswith("/api/gallery-image/"):
            iid = path.split("/")[3]
            row = db.execute("SELECT image_data, image_url FROM generated_images WHERE id=?", (iid,)).fetchone()
            if not row:
                return self._error("image not found", 404)
            if row["image_data"]:
                raw = base64.b64decode(row["image_data"])
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(raw)
                return
            if row["image_url"]:
                target = row["image_url"]
                scheme = urllib.parse.urlparse(target).scheme.lower()
                if scheme not in ("http", "https"):
                    return self._error("image url has unsupported scheme", 400)
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._error("no image data", 404)

        return self._error("not found", 404)

    def do_POST(self):
        path = (self.path or "").rstrip("/") or "/"
        db = get_db()

        if path == "/api/projects":
            return self._create_project()
        if path == "/api/presets":
            return self._create_preset()
        if path == "/api/projects" and False:
            pass
        if re.match(r"^/api/projects/[^/]+/assets$", path):
            return self._upload_asset(path.split("/")[3])
        if re.match(r"^/api/projects/[^/]+/conversations$", path):
            return self._create_conversation(path.split("/")[3])
        if re.match(r"^/api/conversations/[^/]+/messages$", path):
            return self._send_message(path.split("/")[3])
        if path == "/api/generate":
            return self._handle_generate()
        if path == "/api/generate-batch":
            return self._handle_batch_generate()
        if path == "/api/logic":
            return self._handle_logic()
        if path == "/api/test-model":
            return self._test_model()
        if re.match(r"^/api/jobs/[^/]+/retry$", path):
            return self._retry_job(path.split("/")[3])
        if re.match(r"^/api/jobs/[^/]+/cancel$", path):
            return self._cancel_job(path.split("/")[3])

        return self._error("not found", 404)

    def do_PUT(self):
        path = (self.path or "").rstrip("/") or "/"

        if re.match(r"^/api/projects/[^/]+$", path):
            return self._update_project(path.split("/")[3])
        if re.match(r"^/api/presets/[^/]+$", path):
            return self._update_preset(path.split("/")[3])
        if re.match(r"^/api/jobs/[^/]+/cancel$", path):
            return self._cancel_job(path.split("/")[3])
        if re.match(r"^/api/jobs/[^/]+/retry$", path):
            return self._retry_job(path.split("/")[3])

        return self._error("not found", 404)

    def do_DELETE(self):
        path = (self.path or "").rstrip("/") or "/"
        db = get_db()

        if re.match(r"^/api/projects/[^/]+/assets/[^/]+$", path):
            return self._delete_asset(path.split("/")[5])
        if re.match(r"^/api/projects/[^/]+$", path):
            return self._delete_project(path.split("/")[3])
        if re.match(r"^/api/presets/[^/]+$", path):
            return self._delete_preset(path.split("/")[3])
        if re.match(r"^/api/conversations/[^/]+$", path):
            return self._delete_conversation(path.split("/")[3])
        if re.match(r"^/api/gallery/[^/]+$", path):
            return self._delete_gallery_item(path.split("/")[3])

        return self._error("not found", 404)

    def _serve_static(self, path):
        rel = sanitize_static_path(path)
        if not rel:
            return self._error("forbidden", 403)
        f = PROJECT_DIR / rel
        try:
            if not f.is_file() or PROJECT_DIR not in f.resolve().parents:
                return self._error("not found", 404)
        except Exception:
            return self._error("not found", 404)
        ct = STATIC_TYPES.get(f.suffix.lower(), "application/octet-stream")
        raw = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)

    # ---- projects -------------------------------------------------------
    def _create_project(self):
        body, err = self._read_body()
        if err:
            return self._error(err)
        name = (body.get("name") or "").strip()
        if not name:
            return self._error("name is required")
        db = get_db()
        pid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO projects (id, name, description, instructions, active_preset_id) VALUES (?,?,?,?,?)",
            (pid, name[:200], (body.get("description") or "")[:2000],
             (body.get("instructions") or "")[:4000], body.get("active_preset_id") or ""),
        )
        db.commit()
        return self._json(dict_from_row(db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()), 201)

    def _update_project(self, pid):
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            return self._error("project not found", 404)
        updates = {}
        for k in ("name", "description", "active_preset_id", "instructions"):
            if k in body:
                updates[k] = body.get(k) or ""
        if updates:
            updates["updated_at"] = None
            set_clause = ", ".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE projects SET {set_clause} WHERE id=?", list(updates.values()) + [pid])
            db.commit()
        return self._json(dict_from_row(db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()))

    def _delete_project(self, pid):
        db = get_db()
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            return self._error("project not found", 404)
        db.execute("DELETE FROM projects WHERE id=?", (pid,))
        db.commit()
        return self._json({"ok": True})

    # ---- presets --------------------------------------------------------
    PRESET_FIELDS = [
        "logic_provider", "logic_url", "logic_key", "logic_model",
        "image_provider", "image_url", "image_key", "image_model",
        "default_aspect_ratio", "default_resolution", "default_count",
        "default_seed", "default_negative_prompt", "default_quality",
        "default_style", "system_prompt", "json_schema",
    ]

    def _create_preset(self):
        body, err = self._read_body()
        if err:
            return self._error(err)
        name = (body.get("name") or "").strip()
        if not name:
            return self._error("name is required")
        db = get_db()
        prid = str(uuid.uuid4())
        vals = [prid, name[:200]]
        for f in self.PRESET_FIELDS:
            v = body.get(f, "")
            vals.append(str(v) if v is not None else "")
        db.execute(
            f"INSERT INTO presets (id, name, {', '.join(self.PRESET_FIELDS)}) "
            f"VALUES (?, ?, {', '.join('?' * len(self.PRESET_FIELDS))})", vals)
        db.commit()
        row = db.execute("SELECT * FROM presets WHERE id=?", (prid,)).fetchone()
        return self._json(self._preset_out(row), 201)

    def _update_preset(self, prid):
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        if not db.execute("SELECT 1 FROM presets WHERE id=?", (prid,)).fetchone():
            return self._error("preset not found", 404)
        updates = {}
        for k in ["name"] + self.PRESET_FIELDS:
            if k in body:
                updates[k] = body.get(k) if body.get(k) is not None else ""
        # Empty key fields mean "keep the stored key": the client never sees
        # plaintext keys, so it cannot echo them back and must not clobber
        # them with masked placeholders.
        for k in ("logic_key", "image_key"):
            if not updates.get(k):
                updates.pop(k, None)
        if updates:
            updates["updated_at"] = None
            set_clause = ", ".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE presets SET {set_clause} WHERE id=?", list(updates.values()) + [prid])
            db.commit()
        row = db.execute("SELECT * FROM presets WHERE id=?", (prid,)).fetchone()
        return self._json(self._preset_out(row))

    def _delete_preset(self, prid):
        db = get_db()
        if not db.execute("SELECT 1 FROM presets WHERE id=?", (prid,)).fetchone():
            return self._error("preset not found", 404)
        db.execute("UPDATE projects SET active_preset_id='' WHERE active_preset_id=?", (prid,))
        db.execute("DELETE FROM presets WHERE id=?", (prid,))
        db.commit()
        return self._json({"ok": True})

    # ---- assets ---------------------------------------------------------
    def _upload_asset(self, pid):
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            return self._error("project not found", 404)
        name = (body.get("name") or "asset").strip()[:200]
        data_b64 = body.get("data") or ""
        if not data_b64:
            return self._error("data is required")
        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            return self._error("invalid base64 data")
        if len(raw) > 10 * 1024 * 1024:
            return self._error("asset too large (>10MB)")
        ft = (body.get("file_type") or "png").lower()
        if ft not in ("png", "jpg", "jpeg", "webp", "svg"):
            ft = "png"
        aid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO assets (id, project_id, name, file_type, size, description, tags, data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (aid, pid, name, ft, len(raw), (body.get("description") or "")[:1000],
             (body.get("tags") or "")[:500], raw))
        db.commit()
        row = db.execute(
            "SELECT id, project_id, name, file_type, size, description, tags, created_at FROM assets WHERE id=?",
            (aid,)).fetchone()
        return self._json(dict_from_row(row), 201)

    def _delete_asset(self, aid):
        db = get_db()
        if not db.execute("SELECT 1 FROM assets WHERE id=?", (aid,)).fetchone():
            return self._error("asset not found", 404)
        db.execute("DELETE FROM assets WHERE id=?", (aid,))
        db.commit()
        return self._json({"ok": True})

    # ---- conversations --------------------------------------------------
    def _create_conversation(self, pid):
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            return self._error("project not found", 404)
        cid = str(uuid.uuid4())
        db.execute("INSERT INTO conversations (id, project_id, title) VALUES (?,?,?)",
                   (cid, pid, (body.get("title") or "New chat")[:200]))
        db.commit()
        return self._json(dict_from_row(db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()), 201)

    def _delete_conversation(self, cid):
        db = get_db()
        if not db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
            return self._error("conversation not found", 404)
        db.execute("DELETE FROM conversations WHERE id=?", (cid,))
        db.commit()
        return self._json({"ok": True})

    # ---- messages -------------------------------------------------------
    def _send_message(self, cid):
        body, err = self._read_body()
        if err:
            return self._error(err)
        content = (body.get("content") or "").strip()
        if not content:
            return self._error("content is required")
        if len(content) > 8000:
            return self._error("message too long")
        result, e = ChatService().run(cid, content)
        if e:
            return self._error(e[0], e[1])
        return self._json(result)

    # ---- generation -----------------------------------------------------
    def _handle_generate(self):
        """Queue a generation job backed by the project's preset."""
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        project_id = (body.get("project_id") or "").strip()
        prompt = (body.get("prompt") or "").strip()[:4000]
        if not project_id:
            return self._error("project_id is required")
        if not prompt:
            return self._error("prompt is required")
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return self._error("project not found", 404)

        preset = None
        if project["active_preset_id"]:
            preset = db.execute("SELECT * FROM presets WHERE id=?", (project["active_preset_id"],)).fetchone()
        if body.get("preset_id"):
            preset = db.execute("SELECT * FROM presets WHERE id=?", (body["preset_id"],)).fetchone()
        if not preset:
            return self._error("no preset assigned to this project", 400)

        ratio = body.get("aspect_ratio") or body.get("aspectRatio") or "1:1"
        if ratio not in ALLOWED_RATIOS:
            ratio = "1:1"
        try:
            count = max(1, min(4, int(body.get("count", 1))))
        except Exception:
            count = 1

        params = {"prompt": prompt, "aspectRatio": ratio, "count": count,
                  "negative_prompt": (body.get("negative_prompt") or "")[:1000],
                  "size": body.get("size") or RATIO_SIZES[ratio]}
        if body.get("seed") not in (None, ""):
            try:
                params["seed"] = int(body["seed"])
            except Exception:
                pass

        jid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO generation_jobs (id, project_id, conversation_id, message_id, preset_id, prompt, "
            "negative_prompt, parameters_json, status, logic_model, image_model) "
            "VALUES (?,?,?,?,?,?,?,?,'queued',?,?)",
            (jid, project_id, body.get("conversation_id", ""), body.get("message_id", ""), preset["id"],
             prompt, params["negative_prompt"], json.dumps(params),
             preset["logic_model"], preset["image_model"]))
        db.commit()
        log.info("queued job %s (project %s)", jid[:8], project_id[:8])
        return self._json({"job_id": jid, "status": "queued"}, 201)

    def _handle_batch_generate(self):
        body, err = self._read_body()
        if err:
            return self._error(err)
        db = get_db()
        project_id = (body.get("project_id") or "").strip()
        prompts = body.get("prompts")
        if isinstance(prompts, str):
            prompts = [p for p in re.split(r"\n+", prompts) if p.strip()]
        if not project_id:
            return self._error("project_id is required")
        if not prompts or not isinstance(prompts, list):
            return self._error("prompts (non-empty list) is required")
        if len(prompts) > 50:
            return self._error("too many prompts (max 50)")
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return self._error("project not found", 404)
        preset = None
        if body.get("preset_id"):
            preset = db.execute("SELECT * FROM presets WHERE id=?", (body["preset_id"],)).fetchone()
        elif project["active_preset_id"]:
            preset = db.execute("SELECT * FROM presets WHERE id=?", (project["active_preset_id"],)).fetchone()
        if not preset:
            return self._error("no preset assigned to this project", 400)

        ratio = body.get("aspect_ratio") or "1:1"
        if ratio not in ALLOWED_RATIOS:
            ratio = "1:1"
        ids = []
        for p in prompts:
            p = str(p).strip()[:4000]
            if not p:
                continue
            params = {"prompt": p, "aspectRatio": ratio, "count": 1, "size": RATIO_SIZES[ratio]}
            jid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO generation_jobs (id, project_id, conversation_id, message_id, preset_id, prompt, "
                "parameters_json, status, logic_model, image_model) VALUES (?,?,?,?,?,?,?,'queued',?,?)",
                (jid, project_id, body.get("conversation_id", ""), "", preset["id"], p,
                 json.dumps(params), preset["logic_model"], preset["image_model"]))
            ids.append(jid)
        db.commit()
        log.info("queued %d batch jobs", len(ids))
        return self._json({"jobs": ids, "count": len(ids)}, 201)

    def _handle_logic(self):
        """Direct passthrough to a logic model (explicit cfg, used by testing)."""
        body, err = self._read_body()
        if err:
            return self._error(err)
        api_url = (body.get("api_url") or "").strip().rstrip("/")
        api_key = (body.get("api_key") or "").strip()
        messages = body.get("messages") or []
        model = (body.get("model") or "").strip()
        if not api_url or not api_key or not messages:
            return self._error("api_url, api_key and messages are required")
        ok, url_err = validate_upstream_url(api_url)
        if not ok:
            return self._error(url_err)
        data, e = get_adapter(body.get("provider")).chat(
            {"url": api_url, "key": api_key, "model": model}, messages)
        if e:
            return self._error(e, 502)
        return self._json(data)

    def _test_model(self):
        """Connectivity check for an arbitrary endpoint/model (API testing)."""
        body, err = self._read_body()
        if err:
            return self._error(err)
        api_url = (body.get("api_url") or "").strip().rstrip("/")
        api_key = (body.get("api_key") or "").strip()
        model = (body.get("model") or "").strip()
        mtype = body.get("type", "image")
        if not api_url:
            return self._json({"ok": False, "error": "api_url is required", "type": mtype})
        if not api_key:
            return self._json({"ok": False, "error": "api_key is required", "type": mtype})
        ok, url_err = validate_upstream_url(api_url)
        if not ok:
            return self._json({"ok": False, "error": url_err, "type": mtype})

        adapter = get_adapter(body.get("provider"))
        cfg = {"url": api_url, "key": api_key, "model": model}
        if not ALLOW_PRIVATE_UPSTREAM:
            cfg["pinned_ip"], ip_err = resolve_validated_ip(api_url)
            if ip_err:
                return self._json({"ok": False, "error": ip_err, "type": mtype})
        if mtype == "image":
            images, e = adapter.generate(cfg, "a simple test image", {"n": 1, "size": "256x256"})
            if e:
                return self._json({"ok": False, "error": e, "type": "image"})
            return self._json({"ok": True, "type": "image", "detail": "Image model responded successfully"})
        data, e = adapter.chat(cfg, [{"role": "user", "content": "Say hello in one word."}], max_tokens=50)
        if e:
            return self._json({"ok": False, "error": e, "type": "logic"})
        try:
            detail = data["choices"][0]["message"]["content"]
        except Exception:
            detail = "Logic model responded successfully"
        return self._json({"ok": True, "type": "logic", "detail": detail})

    # ---- jobs -----------------------------------------------------------
    def _cancel_job(self, jid):
        db = get_db()
        if not db.execute("SELECT 1 FROM generation_jobs WHERE id=?", (jid,)).fetchone():
            return self._error("job not found", 404)
        _CANCELLED_JOBS.add(jid)
        db.execute("UPDATE generation_jobs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (jid,))
        db.commit()
        return self._json({"ok": True})

    def _retry_job(self, jid):
        db = get_db()
        row = db.execute("SELECT status FROM generation_jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return self._error("job not found", 404)
        if row["status"] in ("queued", "running"):
            return self._error("job is already active", 409)
        _CANCELLED_JOBS.discard(jid)
        db.execute("UPDATE generation_jobs SET status='queued', attempts=0, error='', updated_at=datetime('now') WHERE id=?", (jid,))
        db.commit()
        return self._json({"ok": True})

    def _delete_gallery_item(self, iid):
        db = get_db()
        if not db.execute("SELECT 1 FROM generated_images WHERE id=?", (iid,)).fetchone():
            return self._error("image not found", 404)
        db.execute("DELETE FROM generated_images WHERE id=?", (iid,))
        db.commit()
        return self._json({"ok": True})


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


DEMO_PROJECT = "demo-studio"
DEMO_PRESET = "demo-offline"

DEMO_SEED_PROMPTS = (
    ("halftone hands reaching toward a glowing window, high contrast", "1:1"),
    ("dithered monolith at dusk, 1-bit grain", "3:4"),
    ("retro crt monitor glowing in a dark room, ordered dither", "4:3"),
)


def seed_demo():
    """Make a fresh install usable: an offline preset, a demo project bound to
    it, and a few pre-rendered gallery images. Idempotent — existing data is
    left alone, so this only fills an empty database."""
    db = get_db()

    if not db.execute("SELECT 1 FROM presets WHERE id=?", (DEMO_PRESET,)).fetchone():
        db.execute(
            "INSERT INTO presets (id, name, logic_provider, logic_url, logic_key, logic_model, "
            "image_provider, image_url, image_key, image_model, default_aspect_ratio, "
            "default_resolution, default_count, system_prompt) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (DEMO_PRESET, "Demo (offline, no API key)", "demo-logic", "", "", "demo-planner",
             "demo-image", "", "", "halftone-1", "1:1", "1024x1024", 1,
             "You are a creative image generation assistant. Reply with a JSON plan."),
        )
        log.info("seeded demo preset")

    if not db.execute("SELECT 1 FROM projects WHERE id=?", (DEMO_PROJECT,)).fetchone():
        db.execute(
            "INSERT INTO projects (id, name, description, active_preset_id, instructions) VALUES (?,?,?,?,?)",
            (DEMO_PROJECT, "Demo Studio",
             "Offline sandbox — generates locally with no API key required.",
             DEMO_PRESET,
             "Favor bold, high-contrast, graphic compositions."),
        )
        log.info("seeded demo project")

    if not db.execute("SELECT 1 FROM generated_images WHERE project_id=?", (DEMO_PROJECT,)).fetchone():
        for prompt, ratio in DEMO_SEED_PROMPTS:
            try:
                size = RATIO_SIZES.get(ratio, "1024x1024")
                raw = render_demo_image(prompt, size)
                db.execute(
                    "INSERT INTO generated_images (id, job_id, project_id, conversation_id, preset_id, "
                    "prompt, image_data, image_url, parameters_json, logic_model, image_model, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "", DEMO_PROJECT, "", DEMO_PRESET, prompt,
                     base64.b64encode(raw).decode("ascii"), "",
                     json.dumps({"aspectRatio": ratio, "count": 1, "size": size}),
                     "demo-planner", "halftone-1", "completed"),
                )
            except Exception:
                log.exception("failed to seed demo image")
        log.info("seeded demo gallery")

    db.commit()


if __name__ == "__main__":
    seed_demo()
    start_job_worker()
    log.info("img-gen server -> http://%s:%d", HOST, PORT)
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
