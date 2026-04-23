import os

from env_loader import load_project_dotenv


def test_load_project_dotenv_reads_values_from_explicit_path(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("KIDS_TEACHER_REALTIME_MODEL=gpt-realtime-mini\n", encoding="utf-8")
    monkeypatch.delenv("KIDS_TEACHER_REALTIME_MODEL", raising=False)

    loaded = load_project_dotenv(dotenv_path=dotenv_path)

    assert loaded == dotenv_path.resolve()
    assert os.environ["KIDS_TEACHER_REALTIME_MODEL"] == "gpt-realtime-mini"


def test_load_project_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("KIDS_DEFAULT_EXPLANATION_LANGUAGE=telugu\n", encoding="utf-8")
    monkeypatch.setenv("KIDS_DEFAULT_EXPLANATION_LANGUAGE", "english")

    load_project_dotenv(dotenv_path=dotenv_path)

    assert os.environ["KIDS_DEFAULT_EXPLANATION_LANGUAGE"] == "english"
