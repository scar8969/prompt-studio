"""Run the test suite against a throwaway Postgres, on any machine.

SQLite passes happily while the Postgres code path is broken — that is exactly how a
`dict(row)` on a psycopg2 tuple shipped and 500'd every endpoint. This starts a temporary
cluster, runs the suite against it, and tears it down, so nobody has to keep a database
running to check the engine production uses.

    python scripts/with_postgres.py              # whole suite
    python scripts/with_postgres.py -k storage   # pass pytest args through

Postgres binaries are looked for in this order:
  1. `$PG_BIN` (a directory containing initdb/pg_ctl)
  2. `pg_ctl` on PATH
  3. a previously downloaded cluster under `$TMPDIR/pg17/pgsql/bin`

If none is found, the script says so and exits 2 rather than silently running SQLite —
a green result from the wrong engine is worse than no result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("SUITE_PG_PORT", "5433"))
ROOT = Path(__file__).resolve().parent.parent


def find_bin() -> Path | None:
    explicit = os.environ.get("PG_BIN", "").strip()
    if explicit:
        candidate = Path(explicit)
        if (candidate / "initdb.exe").exists() or (candidate / "initdb").exists():
            return candidate
    on_path = shutil.which("pg_ctl")
    if on_path:
        return Path(on_path).parent
    # A cluster downloaded by an earlier run.
    for base in (Path(tempfile.gettempdir()),):
        for version in ("pg17", "pg16", "pg15"):
            candidate = base / version / "pgsql" / "bin"
            if (candidate / "initdb.exe").exists() or (candidate / "initdb").exists():
                return candidate
    return None


def exe(bin_dir: Path, name: str) -> str:
    candidate = bin_dir / (name + (".exe" if os.name == "nt" else ""))
    return str(candidate)


def download(bin_dir: Path) -> bool:
    """Fetch the Windows binaries if asked. On other platforms ask the user to install."""
    if os.name != "nt":
        print("Postgres not found. Install it and set PG_BIN to its bin directory.",
              file=sys.stderr)
        return False
    target = Path(tempfile.gettempdir()) / "pg17.zip"
    url = "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip"
    print(f"downloading Postgres binaries (~330 MB) to {target} ...", file=sys.stderr)
    try:
        urllib.request.urlretrieve(url, target)
    except Exception as exc:                                   # noqa: BLE001
        print(f"download failed: {exc}", file=sys.stderr)
        return False
    dest = Path(tempfile.gettempdir()) / "pg17"
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["unzip", "-q", "-o", str(target), "-d", str(dest)], check=False)
    return True


def main(argv: list[str]) -> int:
    if any(a in ("--help", "-h") for a in argv):
        print(__doc__)
        return 0

    bin_dir = find_bin()
    if bin_dir is None:
        if "--download" in argv:
            if not download(Path(tempfile.gettempdir()) / "pg17" / "pgsql" / "bin"):
                return 2
            bin_dir = find_bin()
        if bin_dir is None:
            print("No Postgres binaries found.\n"
                  "  Install Postgres and set PG_BIN, or re-run with --download "
                  "(Windows binaries).", file=sys.stderr)
            return 2

    # A port already in use is the most likely failure, so say which one.
    # initdb refuses a directory that already has anything in it, and mkdtemp creates
    # the directory it names. The cluster therefore lives in a fresh child of the temp
    # dir, which initdb is allowed to create itself.
    workspace = Path(tempfile.mkdtemp(prefix="suite-pg-"))
    data_dir = workspace / "data"
    log = workspace / "server.log"
    # Native paths: this tool's shell is bash, but initdb/pg_ctl are native programs and
    # do not accept MSYS-style /tmp paths.
    print(f"starting a scratch cluster in {data_dir}", file=sys.stderr, flush=True)
    print(f"  argv: {[exe(bin_dir, 'initdb'), '-D', str(data_dir), '-U', 'postgres']}",
          file=sys.stderr, flush=True)
    init_log = workspace / "initdb.out"
    with open(init_log, "w", encoding="utf-8") as sink:
        try:
            init = subprocess.run(
                [exe(bin_dir, "initdb"), "-D", str(data_dir), "-U", "postgres",
                 "--auth=trust", "-E", "UTF8"],
                stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=180)
        except subprocess.TimeoutExpired:
            print("initdb did not finish within 180s", file=sys.stderr)
            return 2
    if init.returncode != 0:
        print("initdb failed:", file=sys.stderr)
        print(init_log.read_text(errors="replace")[-1500:], file=sys.stderr)
        return 2
    # `pg_ctl start` waits for the server and returns. Its output goes to a file for the
    # same reason as initdb: capture_output on a native program that starts background
    # children can leave the pipe open after the parent exits, and reading it then waits
    # for a process that will never close it.
    ctl_log = workspace / "pg_ctl.out"
    with open(ctl_log, "w", encoding="utf-8") as sink:
        try:
            start = subprocess.run(
                [exe(bin_dir, "pg_ctl"), "-D", str(data_dir), "-w", "-t", "60",
                 "-o", f"-p {PORT}", "-l", str(log), "start"],
                stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=90)
        except subprocess.TimeoutExpired:
            print(f"pg_ctl did not return within 90s (port {PORT} in use?)",
                  file=sys.stderr)
            if log.exists():
                print(log.read_text(errors="replace")[-600:], file=sys.stderr)
            return 2

    if start.returncode != 0:
        print(ctl_log.read_text(errors="replace")[-800:], file=sys.stderr)
        if log.exists():
            print(log.read_text(errors="replace")[-800:], file=sys.stderr)
        print(f"hint: port {PORT} may already be in use; set SUITE_PG_PORT to another.",
              file=sys.stderr)
        return 2

    url = f"postgresql://postgres@127.0.0.1:{PORT}/postgres"
    try:
        # Wait for the server to accept connections.
        for _ in range(40):
            # -w: never prompt for a password. Without it psql waits on stdin forever,
            # which looks exactly like a database that never became ready.
            try:
                probe = subprocess.run(
                    [exe(bin_dir, "psql"), "-w", "-h", "127.0.0.1", "-p", str(PORT),
                     "-U", "postgres", "-tAc", "SELECT 1"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, timeout=10)
            except subprocess.TimeoutExpired:
                probe = subprocess.CompletedProcess([], 1)
            if probe.returncode == 0:
                break
            time.sleep(0.5)
        else:
            print("the cluster never became ready", file=sys.stderr)
            return 2

        print("  cluster ready; running pytest", file=sys.stderr, flush=True)
        env = dict(os.environ)
        env["TEST_DATABASE_URL"] = url
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("DATABASE_URL", None)
        args = [a for a in argv if a != "--download"]
        targets = [a for a in args if not a.startswith("-")]
        flags = [a for a in args if a.startswith("-")]
        if not targets:
            targets = ["tests/"]
        print(f"  target(s): {targets}", file=sys.stderr, flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider",
             *flags],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL)
        return result.returncode
    finally:
        subprocess.run([exe(bin_dir, "pg_ctl"), "-D", str(data_dir), "-m", "immediate",
                        "stop"], capture_output=True, stdin=subprocess.DEVNULL)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
