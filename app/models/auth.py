from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length = 8, max_length = 128)

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes = True)

    id: str
    email: EmailStr
    is_active: bool
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class RefreshRequest(BaseModel):
    refresh_token: str

class MessageResponse(BaseModel):
    message: str