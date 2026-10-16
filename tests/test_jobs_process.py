import asyncio
import sys

from app.core.jobs import Jobs


async def test_stop_terminates_owned_job_tree_and_releases_reentry(tmp_path):
    recipe = (
        f'kb-preview:\n\t"{sys.executable}" -u -c '
        '"import time; print(12345, flush=True); time.sleep(30)"\n'
    )
    (tmp_path / "Makefile").write_text(recipe, "utf-8")
    jobs = Jobs(tmp_path)
    try:
        await jobs.start("kb-preview")
        async with asyncio.timeout(10):
            while "\n12345" not in jobs.tail("kb-preview").replace("\r", ""):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        assert jobs.states["kb-preview"]["status"] == "running"
        await jobs.stop("kb-preview")
        await asyncio.gather(*jobs.tasks)
        assert jobs.states["kb-preview"]["status"] == "stopped"
        assert jobs.active["kb-preview"].returncode is not None
        (tmp_path / "Makefile").write_text(
            f'kb-preview:\n\t"{sys.executable}" -c "print(67890)"\n', "utf-8"
        )
        await jobs.start("kb-preview")
        await asyncio.gather(*jobs.tasks)
        assert jobs.states["kb-preview"]["status"] == "completed"
        assert "67890" in jobs.tail("kb-preview")
    finally:
        await jobs.close()
