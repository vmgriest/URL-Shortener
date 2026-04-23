from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:password@localhost:5432/urlshortener"
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = "http://localhost:8000"
    rate_limit_capacity: int = 20
    rate_limit_refill_rate: float = 10.0
    sqs_queue_url: str = ""   # set by ECS; empty disables click publishing locally
    dynamodb_table: str = ""  # set by ECS; empty returns 501 on analytics endpoint

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
