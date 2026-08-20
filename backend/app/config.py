from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./analyst.db"
    llm_provider: str = "fake"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    watch_dir: str = "./data/watch"
    fixtures_dir: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:8080"


settings = Settings()
