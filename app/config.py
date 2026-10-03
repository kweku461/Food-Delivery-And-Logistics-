from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "FoodFlow Delivery & Logistics API"
    database_url: str = "sqlite:///./foodflow.db"
    secret_key: str = "dev-only-secret-change-me-in-production-0123456789"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    bcrypt_rounds: int = 12
    first_admin_email: str = "admin@foodflow.com"
    first_admin_password: str = "Admin12345"


settings = Settings()
