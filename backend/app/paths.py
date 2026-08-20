from pathlib import Path

from .config import settings


def fixtures_dir() -> Path:
    if settings.fixtures_dir:
        return Path(settings.fixtures_dir)
    return Path(__file__).resolve().parents[2] / "fixtures"
