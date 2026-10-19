from sqlalchemy.engine import make_url

from app.config import Settings


def test_native_database_endpoints_are_preserved():
    url = "mysql+asyncmy://aicare:local@127.0.0.1:13306/aicare?charset=utf8mb4"
    settings = Settings(_env_file=None, database_url=url, database_host="", database_port=None)
    assert settings.database_url.get_secret_value() == url


def test_container_endpoint_preserves_encoded_credentials_and_database_names():
    settings = Settings(
        _env_file=None,
        database_url="mysql+asyncmy://alice:p%40ss%3Aword%2F%25@127.0.0.1:13306/aicare?charset=utf8mb4",
        test_database_url="mysql+asyncmy://bob:other%40pass@localhost:13306/aicare_test?charset=utf8mb4",
        database_host="mysql",
        database_port=3306,
    )
    main = make_url(settings.database_url.get_secret_value())
    test = make_url(settings.test_database_url.get_secret_value())
    assert (main.host, main.port, main.username, main.password) == (
        "mysql",
        3306,
        "alice",
        "p@ss:word/%",
    )
    assert (test.host, test.port, test.username, test.password) == (
        "mysql",
        3306,
        "bob",
        "other@pass",
    )
    assert (main.database, test.database) == ("aicare", "aicare_test")
    assert main.query == test.query == {"charset": "utf8mb4"}


def test_container_environment_is_used_by_background_job_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_HOST", "mysql")
    monkeypatch.setenv("DATABASE_PORT", "3306")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://aicare:local@localhost:13306/aicare")
    settings = Settings(_env_file=None)
    url = make_url(settings.database_url.get_secret_value())
    assert (url.host, url.port) == ("mysql", 3306)
