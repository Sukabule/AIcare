"""Public static routes must not acquire query params or become a catch-all."""

from pathlib import Path

import httpx

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


async def test_pages_keep_contents_names_and_static_asset_links():
    app = create_app()
    pages = {
        "/": ("index", "index.html"),
        "/admin": ("admin_page", "admin.html"),
        "/kb": ("knowledge_page", "kb.html"),
        "/review": ("review_page", "review.html"),
        "/observability": ("observability_page", "observability.html"),
        "/rag-eval": ("rag_evaluation_page", "rag-eval.html"),
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            schema = (await client.get("/openapi.json")).json()
            for path, (name, filename) in pages.items():
                assert str(app.url_path_for(name)) == path
                response = await client.get(path, params={"filename": "../../.env"})
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/html")
                assert response.content == (ROOT / "app/static" / filename).read_bytes()
                assert path not in schema["paths"]
            assert (await client.get("/unknown-page")).status_code == 404
            for path in ("workspace.css", "admin-base.css", "design.css", "admin.css", "ch09.css"):
                assert (await client.get("/static/" + path)).status_code == 200
    finally:
        await app.state.database.close()
