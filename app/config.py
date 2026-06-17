from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    secret_key: str = "dev-secret-key-change-in-production"
    app_env: str = "development"
    app_name: str = "Espaço Physio Intranet"

    class Config:
        env_file = ".env"


settings = Settings()
