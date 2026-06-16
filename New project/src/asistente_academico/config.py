from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    database_path: str
    calendar_dir: str


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    database_path = os.getenv("DATABASE_PATH", "data/asistente_academico.sqlite3").strip()
    calendar_dir = os.getenv("CALENDAR_DIR", "data/calendarios").strip()

    if not token:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN. Crea un archivo .env usando .env.example."
        )

    return Settings(
        telegram_bot_token=token,
        database_path=database_path,
        calendar_dir=calendar_dir,
    )
