"""Configuración desde .env / variables de entorno."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Busca .env en la raíz del repo (un nivel arriba de ingest/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    pg_host: str = os.getenv("POSTGRES_HOST", "localhost")
    pg_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_db: str = os.getenv("POSTGRES_DB", "argos")
    pg_user: str = os.getenv("POSTGRES_USER", "argos")
    pg_password: str = os.getenv("POSTGRES_PASSWORD", "")
    platform: str = os.getenv("ARGOS_PLATFORM", "bybit")
    min_request_interval: float = float(os.getenv("ARGOS_MIN_REQUEST_INTERVAL", "3.0"))
    poll_interval: int = int(os.getenv("ARGOS_POLL_INTERVAL", "300"))

    @property
    def dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password} options='-c search_path=argos,public'"
        )


settings = Settings()
