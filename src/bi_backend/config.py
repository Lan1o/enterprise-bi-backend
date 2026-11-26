from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BI_", env_file=".env", extra="ignore", validate_assignment=True
    )

    database_url: str = "sqlite:///./bi.db"
    api_key: SecretStr = SecretStr("")
    sources_file: Path = Path("config/sources.demo.yaml")
    allowed_source_hosts: list[str] = ["127.0.0.1", "localhost", "demo-sources"]
    max_records: int = Field(default=10_000, ge=1, le=100_000)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    max_snapshot_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    request_timeout: float = Field(default=10, ge=1, le=30)
    lease_seconds: int = Field(default=300, ge=120)
    stale_days: int = Field(default=30, ge=1)
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
