"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STARTING_CASH = 10_000.0
SNAPSHOT_INTERVAL_SECONDS = 30.0  # single constant for the periodic P&L snapshot task
SNAPSHOT_RETENTION = 20_000
CHAT_HISTORY_LIMIT = 20
DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from the project-root .env without overriding the environment.

    Only for local non-Docker runs; Docker supplies the environment via --env-file.
    """
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def db_path() -> Path:
    return Path(os.environ.get("DB_PATH") or PROJECT_ROOT / "db" / "finally.db")


def static_dir() -> Path:
    return Path(os.environ.get("STATIC_DIR") or PROJECT_ROOT / "backend" / "static")


def llm_mock() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() == "true"
