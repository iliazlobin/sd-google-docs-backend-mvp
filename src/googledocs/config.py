"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    db_url: str = "postgresql+asyncpg://googledocs:googledocs@localhost:5432/googledocs"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_port: int = 8010


settings = Settings()
