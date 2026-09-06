from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "svc-time"
    port: int = 8002
    database_url: str = "postgresql+psycopg://atlas:atlas@localhost:5432/time_db"
    amqp_url: str = "amqp://atlas:atlas@localhost:5672/"
    jwt_secret: str = "atlas-dev-secret"
    jwt_ttl_hours: int = 8


settings = Settings()

