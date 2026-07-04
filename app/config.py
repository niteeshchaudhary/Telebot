from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str = Field(
        default="test_token", description="Telegram Bot Token from BotFather"
    )
    opencode_executable: str = Field(
        default="opencode", description="OpenCode executable name or path"
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///./telebot.db", description="Database URL"
    )
    log_level: str = Field(default="INFO", description="Log level")

    allowed_user_ids_str: str = Field(
        default="", description="Comma-separated list of allowed Telegram user IDs"
    )

    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.allowed_user_ids_str.strip():
            return []
        return [
            int(x.strip()) for x in self.allowed_user_ids_str.split(",") if x.strip()
        ]

    session_idle_timeout: int = Field(
        default=3600, description="Session idle timeout in seconds"
    )
    stream_buffer_size: int = Field(
        default=4096, description="Stream buffer size"
    )
    stream_update_interval: float = Field(
        default=0.5, description="Stream update interval in seconds"
    )
    message_max_length: int = Field(
        default=4096, description="Telegram message max length"
    )
    default_cwd: str = Field(
        default="~", description="Default working directory for new sessions"
    )

    # Webhook settings
    webhook_mode: bool = Field(default=False, description="Enable webhook mode")
    webhook_url: str = Field(default="", description="Public HTTPS webhook URL (e.g. https://domain.com/webhook)")
    webhook_port: int = Field(default=8443, description="Local port to listen on")
    webhook_path: str = Field(default="/webhook", description="Webhook path")
    webhook_secret: str = Field(default="", description="Secret token for webhook validation")


    @property
    def default_cwd_path(self) -> Path:
        return Path(self.default_cwd).expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
