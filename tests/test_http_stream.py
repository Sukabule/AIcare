"""Real loopback HTTP transport verifies the SSE API does not buffer a whole answer."""

import asyncio
import socket
import threading
import time

import httpx
import uvicorn

from app.config import Settings
from app.main import create_app


def test_http_client_receives_first_frame_before_generation_finishes():
    release = threading.Event()
    finished = threading.Event()

    class GatedService:
        async def stream_agent_turn(self, body):
            yield {"event": "tool", "name": "query_logistics"}
            yield {"delta": "first"}
            await asyncio.to_thread(release.wait, 5)
            finished.set()
            yield {"delta": "second"}
            yield {"event": "done", "conversation_id": 12}

    app = create_app(settings=Settings(_env_file=None))
    app.state.agent_factory = lambda: GatedService()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
            ) as client:
                with client.stream(
                    "POST", "/api/chat", json={"user_id": "u1", "message": "hi"}
                ) as response:
                    lines = response.iter_lines()
                    assert next(lines) == 'data: {"event": "tool", "name": "query_logistics"}'
                    assert next(lines) == ""
                    assert next(lines) == 'data: {"delta": "first"}'
                    assert not finished.is_set()
                    release.set()
                    rest = list(lines)
                    assert 'data: {"delta": "second"}' in rest
                    assert "data: [DONE]" in rest
        finally:
            release.set()
            server.should_exit = True
            thread.join(timeout=6)
        assert not thread.is_alive()
