from pathlib import Path

import app.config
from app.config import load_settings

REPO = Path(__file__).resolve().parents[2]


def test_raw_data_dir_defaults_under_repo_data(monkeypatch) -> None:
    monkeypatch.delenv("RAW_DATA_DIR", raising=False)
    assert Path(load_settings().raw_data_dir) == REPO / "data" / "raw"


def test_raw_data_dir_can_be_overridden(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path))
    assert load_settings().raw_data_dir == str(tmp_path)


def test_env_example_has_no_paid_only_books_and_enables_persistence() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    books_line = next(line for line in text.splitlines() if line.startswith("ODDS_BOOKMAKERS="))
    books = books_line.split("=", 1)[1].split(",")
    assert books == ["draftkings", "fanduel", "betmgm", "betrivers", "espnbet"]
    assert "PERSISTENCE_ENABLED=true" in text


def test_ci_never_deploys() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci-deploy.yml").read_text(encoding="utf-8")
    assert "flyctl" not in workflow
    assert "deploy:" not in workflow


def test_dotenv_is_isolated_for_tests() -> None:
    assert app.config._DOTENV_PATH.name == "does-not-exist.env"
