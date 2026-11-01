from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Lens"
    host: str = "127.0.0.1"
    port: int = 18080
    auth_secret_key: str = ""
    auth_access_token_minutes: int = 60 * 12
    request_timeout_seconds: float = 180.0
    connect_timeout_seconds: float = 10.0
    max_request_body_bytes: int = 32_000_000
    max_connections: int = 200
    max_keepalive_connections: int = 50
    database_url: str
    # Every admin list endpoint used to run long scans; the default 5+10 pool
    # saturated as soon as one browser had a few tabs polling.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: float = 30.0
    db_pool_recycle_seconds: int = 1800
    workers: int = 1
    anthropic_version: str = "2023-06-01"
    ui_static_dir: str = ""

    model_config = SettingsConfigDict(
        env_prefix="LENS_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
