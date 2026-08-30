from typing import Optional
from sqlmodel import SQLModel, Field, BigInteger


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: Optional[int] =  Field(
        primary_key=True,
        sa_type=BigInteger,
        default=None
    )
    email: str = Field(
        max_length=100,
        nullable=False,
        unique=True,
        index=True
    )