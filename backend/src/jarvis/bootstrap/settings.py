"""Configuração da instalação local; a demonstração SQLite exige opção explícita."""

import os
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[4]


class ConfigurationError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    jarvis_database_url: SecretStr | None = None
    jarvis_default_owner_name: str = "Álvaro"
    postgres_db: str = "jarvis"
    postgres_user: str = "jarvis"
    postgres_password: SecretStr | None = None
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 55432

    def database_url(self) -> URL:
        if self.jarvis_database_url:
            try:
                url = make_url(self.jarvis_database_url.get_secret_value())
            except Exception:
                raise ConfigurationError("URL de banco inválida.") from None
            if url.get_backend_name() == "sqlite":
                return url
            if url.drivername in {"postgresql", "postgresql+psycopg"} and url.password:
                return url.set(drivername="postgresql+psycopg")
            raise ConfigurationError("Configure SQLite ou PostgreSQL com senha.")
        password = self.postgres_password.get_secret_value() if self.postgres_password else ""
        if not password.strip():
            raise ConfigurationError("Defina POSTGRES_PASSWORD ou um banco SQLite de demonstração.")
        return URL.create(
            "postgresql+psycopg",
            self.postgres_user,
            password,
            self.postgres_host,
            self.postgres_port,
            self.postgres_db,
        )


def read_settings() -> Settings:
    try:
        return Settings(_env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))  # pyright: ignore[reportCallIssue]
    except ValidationError:
        raise ConfigurationError("Configuração local inválida. Confira o arquivo .env.") from None
