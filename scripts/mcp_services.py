"""Start/stop the two registered MCP processes, with verified PID ownership."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVERS = {"logistics": ("logistics_server.py", 8101), "aftersales": ("aftersales_server.py", 8102)}
LOG = ROOT / "log" / "mcp"


def command_line(pid):
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine",
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
        )
        return result.stdout
    result = subprocess.run(
        ["ps", "-p", str(int(pid)), "-o", "args="], capture_output=True, text=True, check=False
    )
    return result.stdout


def owned(pid, script):
    line = command_line(pid)
    return str(script).lower() in line.lower() and str(Path(sys.executable)).lower() in line.lower()


def stop_process(pid, script):
    if not owned(pid, script):
        return
    if os.name == "nt":
        # venv/python.exe is a launcher on Windows; terminate its verified
        # script child as well, otherwise it can leave the listening server alive.
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(ROOT / "scripts/stop_mcp.ps1"),
                "-TaskProcessId",
                str(pid),
                "-TaskScriptPath",
                str(script),
                "-TaskExecutablePath",
                sys.executable,
            ],
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.kill(pid, signal.SIGTERM)


def start(selection="all"):
    LOG.mkdir(parents=True, exist_ok=True)
    for name, (file, port) in SERVERS.items():
        if selection != "all" and name != selection:
            continue
        path, script = LOG / f"{name}.pid.json", ROOT / "mcp_servers" / file
        if path.exists():
            state = json.loads(path.read_text("utf-8"))
            if owned(state["pid"], script):
                print(f"{name}: already running ({state['pid']})")
                continue
        # Do not hijack an occupied port owned by another application.
        import socket

        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied; not managed by this launcher")
        with (LOG / f"{name}.log").open("ab") as stream:
            process = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=ROOT,
                env={**os.environ, "MCP_PORT": str(port), "PYTHONIOENCODING": "utf-8"},
                stdout=stream,
                stderr=stream,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        path.write_text(json.dumps({"pid": process.pid}), encoding="utf-8")
        print(f"{name}: started ({process.pid}), http://127.0.0.1:{port}/mcp")
        # Wait only for the local socket; the protocol smoke below verifies tools.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited; inspect {LOG / (name + '.log')}")
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise TimeoutError(f"{name} startup timed out")


def stop(selection="all"):
    for name, (file, _) in SERVERS.items():
        if selection != "all" and name != selection:
            continue
        path, script = LOG / f"{name}.pid.json", ROOT / "mcp_servers" / file
        if not path.exists():
            continue
        state = json.loads(path.read_text("utf-8"))
        pid = int(state["pid"])
        if owned(pid, script):
            stop_process(pid, script)
            print(f"{name}: stopped ({pid})")
        else:
            print(f"{name}: stale PID, skipped termination")
        path.unlink()


async def smoke():
    sys.path.insert(0, str(ROOT))
    from app.config import Settings
    from app.tools.mcp_client import MCPDiscovery

    specs = await MCPDiscovery(Settings()).fetch_mcp_specs()
    names = {spec.name for spec in specs}
    assert {"query_logistics", "query_warranty", "query_return_status"} <= names, names
    for spec in specs:
        arguments = (
            {"tracking_no": "SF1001"} if spec.name == "query_logistics" else {"order_id": "1001"}
        )
        result = spec.format_result(await spec.tool.ainvoke(arguments))
        print(
            json.dumps(
                {"name": spec.name, "source": spec.mcp_server, "result": result}, ensure_ascii=False
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "restart", "smoke"])
    parser.add_argument("--server", choices=["all", *SERVERS], default="all")
    args = parser.parse_args()
    action = args.action
    if action == "smoke":
        asyncio.run(smoke())
    else:
        if action in ("down", "restart"):
            stop(args.server)
        if action in ("up", "restart"):
            start(args.server)
