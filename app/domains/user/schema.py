from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
)


class UserResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: int
    email: EmailStr
    name: str
    profile_image_url: str | None
    email_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime