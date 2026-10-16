import asyncio
import os
import shutil
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

TARGETS = {
    name: ["make", name]
    for name in (
        "kb-preview",
        "kb-build",
        "seed-conv",
        "kb-mine",
        "kb-vectorize",
        "kb-reset",
        "eval-retrieval",
        "eval-rag",
        "judge-check",
        "flywheel",
        "cost-report",
        "eval-flywheel",
        "calibrate-confidence",
    )
}
HEAVY = {
    "kb-mine",
    "kb-reset",
    "eval-rag",
    "judge-check",
    "flywheel",
    "eval-flywheel",
    "calibrate-confidence",
}


class Jobs:
    def __init__(self, root: Path):
        self.root = root
        self.active = {}
        self.states = {}
        self.tasks = set()
        self.lock = asyncio.Lock()

    def status(self):
        return [
            dict(name=name, heavy=name in HEAVY, **self.states.get(name, {"status": "idle"}))
            for name in TARGETS
        ]

    async def start(self, name):
        if name not in TARGETS:
            raise KeyError(name)
        async with self.lock:
            if name in self.active and self.active[name].returncode is None:
                raise BlockingIOError("同名作业正在运行")
            directory = self.root / "log" / "acceptance"
            directory.mkdir(parents=True, exist_ok=True)
            stream = (directory / f"{name}.log").open("wb")
            options = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            try:
                argv = list(TARGETS[name])
                portable = self.root / ".cache" / "bin" / "make.exe"
                if os.name == "nt" and not shutil.which("make") and portable.is_file():
                    argv[0] = str(portable)
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=self.root,
                    stdout=stream,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                    **options,
                )
            except BaseException:
                stream.close()
                raise
            self.active[name] = process
            self.states[name] = dict(status="running", started_at=datetime.now(UTC).isoformat())
            task = asyncio.create_task(self._watch(name, process, stream))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        return self.states[name]

    async def _watch(self, name, process, stream):
        try:
            code = await process.wait()
            state = self.states[name]
            state.update(
                status="stopped"
                if state.get("stopping")
                else "completed"
                if code == 0
                else "failed",
                exit_code=code,
            )
        finally:
            stream.close()

    def tail(self, name):
        if name not in TARGETS:
            raise KeyError(name)
        path = self.root / "log" / "acceptance" / f"{name}.log"
        if not path.exists():
            return ""
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - 16000))
            return f.read().decode("utf-8", errors="replace")

    async def stop(self, name):
        if name not in TARGETS:
            raise KeyError(name)
        process = self.active.get(name)
        if process is None or process.returncode is not None:
            return
        self.states[name]["stopping"] = True
        if os.name == "nt":
            # Only our tracked process and descendants; never a process-name wildcard.
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    async def close(self):
        for name in tuple(self.active):
            await self.stop(name)
        if self.tasks:
            await asyncio.gather(*self.tasks)
