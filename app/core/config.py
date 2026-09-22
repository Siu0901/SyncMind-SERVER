from functools import lru_cache
from pathlib import Path
from pydantic import SecretStr, EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
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

    #aws
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: SecretStr
    AWS_REGION: str
    AWS_S3_BUCKET: str

    # llm/embed-api + parameter
    OPENAI_EMBED_MODEL: str
    OPENAI_LLM_MODEL: str
    OPENAI_EMBED_MODEL: str
    OPENAI_EMBED_DIM: int
    OPENAI_EMBED_BATCH_SIZE: int

    # 업로드 문서 관련 데이터들
    #---------------------
    # 업로드 가능한 문서들임. 일단 이정도만
    ALLOWED_CONTENT_TYPES: set = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    }
    # 그 파일 최대 크기임 20MB
    MAX_FILE_SIZE: int = 20 * 1024 * 1024

    MAX_PAGES: int = 500
    GRAPHICS_LIMIT: int = 5000
    HANGUL_START: int = 0xAC00
    HANGUL_END: int = 0xD7A3
    #----------------------

    # 청킹
    ENCODING_NAME: str
    MAX_TOKENS: int = 768 # 512가 기본인데 한국어는 영어보다 토큰 딸려서 768 정도로 높여야 될듯.
    MIN_TOKENS: int = 96
    OVERLAP_RATIO: float = 0.12 # 오버랩 10~15%가 좋다해서 적절하게 12% 정도로 함
    MAX_PROTECTED_TOKENS: int = 2048


@lru_cache
def get_settings() -> Settings:
    return Settings()