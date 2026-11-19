"""Start KhataShop.

  python run.py          Production server (standard-library threaded server) under a
                         supervisor that restarts it automatically when code or
                         db_config.json changes, and if it ever crashes.
  python run.py --dev    The same web server without the supervisor (no auto restart).
  python run.py --check  Validate db_config.json and the database connection, then exit.

How the auto-restart works (production mode):
  * The supervisor polls the watched folders (server.watch_dirs) every second.
    Polling is used on purpose: it works on every OS and inside synced folders
    such as OneDrive, where file-system events are unreliable.
  * On a change it first test-loads the new code in a separate process. If the
    new code fails to import (syntax error, bad config, ...), the error is
    logged and the running server is left alone, so the site stays up.
  * If the test passes, the old server is stopped and a new one started.
  * If the server process dies unexpectedly it is restarted, with a pause when
    it keeps crashing (server.max_crash_restarts_per_minute).
"""
from __future__ import annotations
import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from app.config import ConfigError, config_path, load_config  # noqa: E402

EXIT_CONFIG_ERROR = 3
log = logging.getLogger("khatashop.supervisor")


# ------------------------------------------------------------------ helpers --

def setup_supervisor_logging(cfg: dict) -> None:
    log_file = Path(cfg["logging"]["file"]).with_name("supervisor.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] supervisor: %(message)s")
    handlers = [logging.StreamHandler(sys.stdout), RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def load_config_or_exit() -> dict:
    try:
        return load_config()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n  {exc}\n", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)


def snapshot(cfg: dict) -> dict[str, float]:
    """mtime of every watched file, plus db_config.json."""
    exts = {e.lower() for e in cfg["server"]["watch_extensions"]}
    files: dict[str, float] = {}
    for folder in cfg["server"]["watch_dirs"]:
        root = (BASE_DIR / folder).resolve()
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "node_modules", ".venv")]
            for name in filenames:
                if Path(name).suffix.lower() in exts and not name.endswith(".uploading"):
                    path = os.path.join(dirpath, name)
                    try:
                        files[path] = os.stat(path).st_mtime
                    except OSError:
                        pass
    cfg_file = config_path()
    try:
        files[str(cfg_file)] = os.stat(cfg_file).st_mtime
    except OSError:
        pass
    return files


def changed_files(old: dict, new: dict) -> list[str]:
    changed = [p for p, m in new.items() if old.get(p) != m]
    removed = [p for p in old if p not in new]
    return sorted(changed + removed)


def preflight() -> tuple[bool, str]:
    """Test-load the app in a separate process. Returns (ok, error output)."""
    code = "import sys; sys.path.insert(0, r'%s'); from app import create_app; create_app()" % BASE_DIR
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=BASE_DIR, capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return False, "Loading the app took longer than 60 seconds."
    if result.returncode == 0:
        return True, ""
    lines = (result.stderr or result.stdout).strip().splitlines()
    return False, "\n".join(lines[-15:])


# --------------------------------------------------------------- supervisor --

class Supervisor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.child: subprocess.Popen | None = None
        self.crash_times: list[float] = []
        self.stopping = False

    def start_child(self) -> None:
        self.child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve-child"], cwd=BASE_DIR)
        log.info("Server started (pid %s) on http://%s:%s", self.child.pid, self.cfg["app"]["host"], self.cfg["app"]["port"])

    def stop_child(self) -> None:
        if not self.child or self.child.poll() is not None:
            self.child = None
            return
        log.info("Stopping server (pid %s)", self.child.pid)
        self.child.terminate()
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("Server did not stop in 10s; killing it")
            self.child.kill()
            self.child.wait(timeout=5)
        self.child = None

    def reload_config(self) -> bool:
        try:
            self.cfg = load_config()
            return True
        except ConfigError as exc:
            log.error("db_config.json is invalid, keeping the current server running: %s", exc)
            return False

    def run(self) -> None:
        server_cfg = self.cfg["server"]
        files = snapshot(self.cfg)
        self.start_child()
        waiting_for_fix = False

        while not self.stopping:
            time.sleep(1)

            # 1. Code or config changed?
            if server_cfg["auto_restart"]:
                current = snapshot(self.cfg)
                changes = changed_files(files, current)
                if changes:
                    time.sleep(server_cfg["restart_delay_seconds"])  # let editors finish writing
                    current = snapshot(self.cfg)
                    changes = changed_files(files, current) or changes
                    files = current
                    shown = ", ".join(os.path.relpath(c, BASE_DIR) for c in changes[:5])
                    more = f" and {len(changes) - 5} more" if len(changes) > 5 else ""
                    log.info("Change detected: %s%s", shown, more)
                    if not self.reload_config():
                        continue
                    ok, error = preflight()
                    if not ok:
                        log.error("New code failed to load; the running server was NOT restarted.\n%s", error)
                        waiting_for_fix = True
                        continue
                    waiting_for_fix = False
                    self.stop_child()
                    self.crash_times.clear()
                    self.start_child()
                    server_cfg = self.cfg["server"]
                    continue

            # 2. Did the server die?
            if self.child and self.child.poll() is not None:
                code = self.child.returncode
                self.child = None
                if code == EXIT_CONFIG_ERROR or waiting_for_fix:
                    log.error("Server stopped (exit %s). Waiting for the code/config to be fixed before starting again.", code)
                    waiting_for_fix = True
                    continue
                now = time.time()
                self.crash_times = [t for t in self.crash_times if now - t < 60] + [now]
                if len(self.crash_times) > server_cfg["max_crash_restarts_per_minute"]:
                    log.error("Server crashed %s times in a minute; pausing 30 seconds before trying again.", len(self.crash_times))
                    time.sleep(30)
                    self.crash_times.clear()
                log.warning("Server exited unexpectedly (exit code %s); restarting", code)
                self.start_child()
            elif self.child is None and waiting_for_fix is False and not self.stopping:
                self.start_child()

    def shutdown(self, *_):
        self.stopping = True
        self.stop_child()


# ------------------------------------------------------------------- modes --

def serve_child() -> None:
    """Runs inside the supervised process: the real web server."""
    from app import create_app
    from app.core.server import serve

    try:
        app = create_app()
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    cfg = app.config["KS"]
    try:
        serve(app, host=cfg["app"]["host"], port=cfg["app"]["port"], threads=cfg["server"]["threads"])
    except OSError as exc:
        logging.getLogger("khatashop").critical("Could not start the web server on port %s: %s", cfg["app"]["port"], exc)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def run_dev() -> None:
    from app import create_app

    try:
        app = create_app()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n  {exc}\n", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    cfg = app.config["KS"]
    from app.core.server import serve
    # No auto-reload in this mode; use `python run.py` (the supervisor) for restart-on-change.
    serve(app, host=cfg["app"]["host"], port=cfg["app"]["port"], threads=cfg["server"]["threads"])


def run_check() -> None:
    cfg = load_config_or_exit()
    print(f"Config OK: {cfg['_path']}")
    from app import create_app, db

    app = create_app()
    with app.app_context():
        try:
            version = db.fetch_value("SELECT VERSION() AS v")
            bills = db.fetch_value("SELECT COUNT(*) AS c FROM bill")
            missing = db.missing_app_tables()
        except Exception as exc:  # noqa: BLE001 - report whatever went wrong
            print(f"Database check FAILED: {getattr(exc, 'message', exc)}", file=sys.stderr)
            sys.exit(1)
    print(f"Database OK: {cfg['database']['name']} on {cfg['database']['host']} (server {version}, {bills} bills)")
    if missing:
        print(f"App tables MISSING: {', '.join(missing)}\n  Run deploy/001_khatashop_python_tables.sql on this database "
              "(auto_create_tables is " + ("on" if cfg["database"].get("auto_create_tables") else "off") + ").", file=sys.stderr)
    else:
        print("App tables OK: all 8 ks_ tables present")
    upl = Path(cfg["uploads"]["base_dir"])
    print(f"Uploads folder: {upl} ({'found' if upl.is_dir() else 'will be created on first upload'})")
    from app.core import passwords
    if passwords.BACKEND == "pure-python":
        print("Password check: pure Python (works, but each sign-in takes several seconds; this system's crypt has no bcrypt)")
    else:
        print(f"Password check: fast ({'system crypt' if passwords.BACKEND == 'crypt' else 'bcrypt package'})")


def run_create_super_admin(email: str, name: str, password: str | None) -> None:
    import secrets

    from app import create_app
    from app.errors import AppError
    from app.models import super_admin

    app = create_app()
    generated = not password
    password = password or secrets.token_urlsafe(9)
    with app.app_context():
        try:
            new_id = super_admin.create(name or email.split("@")[0], email, password)
        except AppError as exc:
            detail = "; ".join(f"{k}: {v}" for k, v in exc.errors.items()) or exc.message
            print(f"Could not create super admin: {detail}", file=sys.stderr)
            sys.exit(1)
    print(f"Super admin created (id {new_id}).\n  Email:    {email.lower()}")
    if generated:
        print(f"  Password: {password}\n  (generated - write it down; it is not shown again. Change it under Super Admins.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="KhataShop web server")
    parser.add_argument("--dev", action="store_true", help="development server with debugger and auto reload")
    parser.add_argument("--check", action="store_true", help="check configuration and database, then exit")
    parser.add_argument("--create-super-admin", metavar="EMAIL", help="create a super admin account, then exit")
    parser.add_argument("--name", help="name for --create-super-admin")
    parser.add_argument("--password", help="password for --create-super-admin (a strong one is generated if omitted)")
    parser.add_argument("--serve-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.create_super_admin:
        run_create_super_admin(args.create_super_admin, args.name, args.password)
    elif args.serve_child:
        serve_child()
    elif args.dev:
        run_dev()
    elif args.check:
        run_check()
    else:
        cfg = load_config_or_exit()
        setup_supervisor_logging(cfg)
        supervisor = Supervisor(cfg)
        signal.signal(signal.SIGINT, supervisor.shutdown)
        signal.signal(signal.SIGTERM, supervisor.shutdown)
        log.info("KhataShop supervisor running (auto restart: %s). Press Ctrl+C to stop.", cfg["server"]["auto_restart"])
        try:
            supervisor.run()
        finally:
            supervisor.shutdown()
            log.info("Stopped.")


if __name__ == "__main__":
    main()
