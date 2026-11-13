"""登录前启动网站与隧道；仅依赖 Python 标准库，配置和日志留在 data/boot。"""
import argparse
import json
import os
from pathlib import Path
import site
import socket
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            return bool(json.load(response).get("ok"))
    except Exception:
        return False


def preflight(cfg):
    """在实际服务账号下读凭据、导入依赖并验证数据目录可写，不启动公网入口。"""
    project = Path(cfg["project"])
    os.chdir(project)
    site.addsitedir(cfg["site_packages"])
    for key in ("python", "tunnel_exe", "tunnel_config", "tunnel_credentials"):
        with open(cfg[key], "rb") as handle:
            handle.read(1)
    with (project / ".env").open("rb") as handle:
        handle.read(1)
    import uvicorn  # noqa: F401
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    for path in [project / "data/app.db", *(project / "data/users").glob("*/learning.db")]:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("数据库检查失败")
    probe = Path(cfg["runtime"]) / "write-check.tmp"
    probe.write_text("ok", encoding="ascii")
    probe.unlink()
    return {"ok": True, "account": os.environ.get("USERNAME", ""), "pid": os.getpid()}


class Supervisor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.runtime = Path(cfg["runtime"])
        self.children = {}
        self.retry = {}
        self.logs = {}

    def log(self, message):
        with (self.runtime / "supervisor.log").open("a", encoding="utf-8") as output:
            output.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")

    def start(self, name, command):
        if time.monotonic() < self.retry.get(name, 0):
            return
        path = self.runtime / (name + ".log")
        if path.exists() and path.stat().st_size > 10 * 1024 * 1024:
            path.replace(self.runtime / (name + "-" + str(int(time.time())) + ".log"))
        output = path.open("ab", buffering=0)
        self.logs[name] = output
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            self.children[name] = subprocess.Popen(command, cwd=self.cfg["project"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, creationflags=flags)
            self.log(f"started {name} pid={self.children[name].pid}")
        except Exception as error:
            output.close()
            self.logs.pop(name, None)
            self.log(f"cannot start {name}: {error}")
            self.retry[name] = time.monotonic() + 10

    def running(self, name):
        process = self.children.get(name)
        if process is None:
            return False
        if process.poll() is None:
            return True
        self.log(f"exited {name} code={process.returncode}")
        self.children.pop(name, None)
        handle = self.logs.pop(name, None)
        if handle:
            handle.close()
        self.retry[name] = time.monotonic() + 10
        return False

    def stop(self, name):
        process = self.children.pop(name, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            self.log(f"stopped {name}")
        handle = self.logs.pop(name, None)
        if handle:
            handle.close()

    def run(self):
        guard = socket.socket()
        # 与旧守护进程共用锁端口，避免登录后启动第二套网站。
        guard.bind(("127.0.0.1", self.cfg["port"] + 1))
        guard.listen(1)
        bootstrap = "import sys,site,runpy;site.addsitedir(sys.argv.pop(1));runpy.run_module('uvicorn',run_name='__main__')"
        backend = [self.cfg["python"], "-u", "-c", bootstrap, self.cfg["site_packages"],
                   "app.main:app", "--host", "0.0.0.0", "--port", str(self.cfg["port"]), "--no-use-colors"]
        tunnel = [self.cfg["tunnel_exe"], "--config", self.cfg["tunnel_config"],
                  "--no-autoupdate", "tunnel", "run", self.cfg["tunnel_id"]]
        self.log("supervisor started account=" + os.environ.get("USERNAME", ""))
        try:
            while not (self.runtime / "stop.flag").exists():
                if not self.running("backend"):
                    self.start("backend", backend)
                ok = healthy(self.cfg["port"])
                disabled = (self.runtime / "tunnel.disabled").exists()
                if disabled:
                    self.stop("tunnel")
                elif ok and not self.running("tunnel"):
                    self.start("tunnel", tunnel)
                state = {"at": datetime.now(timezone.utc).isoformat(), "supervisor_pid": os.getpid(),
                         "account": os.environ.get("USERNAME", ""), "local_ok": ok,
                         "tunnel_disabled": disabled,
                         **{name + "_pid": p.pid for name, p in self.children.items() if p.poll() is None}}
                try:
                    write_json(self.runtime / "status.json", state)
                except OSError as error:
                    self.log("status update failed: " + str(error))
                time.sleep(2)
        finally:
            self.stop("tunnel")
            self.stop("backend")
            guard.close()
            write_json(self.runtime / "status.json", {"stopped": True, "at": datetime.now(timezone.utc).isoformat()})
            for handle in self.logs.values():
                handle.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    Path(cfg["runtime"]).mkdir(parents=True, exist_ok=True)
    if args.check:
        try:
            result = preflight(cfg)
        except Exception as error:
            result = {"ok": False, "error": str(error), "account": os.environ.get("USERNAME", "")}
        write_json(Path(cfg["runtime"]) / "preflight.json", result)
        return 0 if result["ok"] else 1
    Supervisor(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
