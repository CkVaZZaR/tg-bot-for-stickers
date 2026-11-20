"""Настройки из config.env (KEY=VALUE). Переменные окружения имеют приоритет."""

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    ffmpeg: str
    data_dir: Path
    work_dir: Path
    pack_title: str
    default_emoji: str
    gif_max_seconds: int


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_config() -> Config:
    env = _read_env_file(Path(os.environ.get("TG_STICKER_CONFIG", BASE_DIR / "config.env")))

    def get(key: str, default: str | None = None) -> str:
        value = os.environ.get(key) or env.get(key) or default
        if not value:
            raise SystemExit(f"Не задан {key} в config.env")
        return value

    data_dir = Path(get("DATA_DIR", str(BASE_DIR / "data")))
    return Config(
        bot_token=get("BOT_TOKEN"),
        owner_id=int(get("OWNER_ID")),
        ffmpeg=get("FFMPEG", "ffmpeg"),
        data_dir=data_dir,
        work_dir=Path(get("WORK_DIR", str(data_dir / "work"))),
        pack_title=get("PACK_TITLE", "Мои стикеры"),
        default_emoji=get("DEFAULT_EMOJI", "🙂"),
        gif_max_seconds=int(get("GIF_MAX_SECONDS", "30")),
    )
