import os
from functools import lru_cache
from pydantic import SecretStr, EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # db
    DATABASE_URL: str
    DB_ECHO: bool
    DB_POOL_SIZE: int
    DB_MAX_OVERFLOW: int

    # redis
    REDIS_URL: str

    #qdrant
    QDRANT_URL: str
    QDRANT_API_KEY: str
    QDRANT_COLLECTION: str
    EMBEDDING_DIMENSION: int

    #token
    SECRET_KEY: str
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAY: int

    #oauth
    #---------
    #google
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    GOOGLE_AUTH_URL: str
    GOOGLE_TOKEN_URL: str
    GOOGLE_USERINFO_URL: str

    #github
    GITHUB_CLIENT_ID: str
    GITHUB_CLIENT_SECRET: str
    GITHUB_REDIRECT_URI: str
    GITHUB_AUTH_URL: str
    GITHUB_TOKEN_URL: str
    GITHUB_USER_URL: str
    GITHUB_EMAILS_URL: str

    FRONTEND_OAUTH_CALLBACK_URL: str
    #---------

    #email
    SMTP_PASSWORD: SecretStr
    SMTP_FROM_EMAIL: EmailStr
    SMTP_USERNAME: str
    SMTP_PORT: int
    SMTP_HOST: str


@lru_cache
def get_settings() -> Settings:
    return Settings()