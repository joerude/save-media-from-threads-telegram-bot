"""Configuration management."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation."""

    # Bot configuration
    telegram_bot_token: str = Field(..., description="Telegram bot token from @BotFather")

    # Directories
    cache_dir: Path = Field(default=Path("./cache"), description="Directory for cached media")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )

    # Scraping configuration
    max_retries: int = Field(default=3, ge=1, le=10, description="Max retry attempts")
    browser_timeout: int = Field(
        default=60000, ge=5000, le=120000, description="Browser timeout in milliseconds"
    )

    # Telegram limits (constants)
    telegram_max_file_size: int = Field(
        default=50 * 1024 * 1024, description="Max file size (50MB)"
    )
    telegram_max_photo_size: int = Field(
        default=10 * 1024 * 1024, description="Max photo size (10MB)"
    )
    telegram_max_media_group: int = Field(default=10, description="Max media group size")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cache_dir")
    @classmethod
    def create_cache_dir(cls, v: Path) -> Path:
        """Ensure cache directory exists."""
        v.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate bot token format."""
        if not v or len(v) < 10:
            raise ValueError("Invalid Telegram bot token")
        return v


# Global settings instance
settings = Settings()

# Backwards compatibility exports
TELEGRAM_BOT_TOKEN = settings.telegram_bot_token
CACHE_DIR = settings.cache_dir
LOG_LEVEL = settings.log_level
MAX_RETRIES = settings.max_retries
BROWSER_TIMEOUT = settings.browser_timeout
TELEGRAM_MAX_FILE_SIZE = settings.telegram_max_file_size
TELEGRAM_MAX_PHOTO_SIZE = settings.telegram_max_photo_size
TELEGRAM_MAX_MEDIA_GROUP = settings.telegram_max_media_group
