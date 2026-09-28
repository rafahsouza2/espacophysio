from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    secret_key: str = "dev-secret-key-change-in-production"
    app_env: str = "development"
    app_name: str = "Espaço Physio Intranet"

    # Integração iGut (CPF dos pacientes) — docs: https://api.igut.med.br/docs/
    igut_api_url: str = "https://api.igut.med.br"
    igut_clinica: str = "espacophysio"   # vai em Base64 no header client_token
    igut_api_user: str = "api@espacophysio"
    igut_api_password: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
