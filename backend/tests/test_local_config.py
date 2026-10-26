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


def test_ci_deploys_only_from_master_pushes() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci-deploy.yml").read_text(encoding="utf-8")
    deploy = workflow.split("\n  deploy:\n", 1)[1]
    assert "if: github.ref == 'refs/heads/master' && github.event_name == 'push'" in deploy
    assert "needs: [backend-test, frontend-build]" in deploy


def test_fly_keeps_job_state_on_its_volume() -> None:
    fly = (REPO / "fly.toml").read_text(encoding="utf-8")
    assert 'destination = "/data"' in fly
    assert 'RAW_DATA_DIR = "/data/raw"' in fly
    assert 'NBA_PREDICTIONS_DIR = "/data/predictions"' in fly


def test_dotenv_is_isolated_for_tests() -> None:
    assert app.config._DOTENV_PATH.name == "does-not-exist.env"
