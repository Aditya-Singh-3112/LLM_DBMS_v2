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
    email_verified: bool = False
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    # Only populated internally; the API sets the refresh token as an
    # HttpOnly cookie and never returns it in the body.
    refresh_token: str | None = Field(default=None, exclude=True)
    token_type: str = "bearer"
    expires_in: int

class RefreshRequest(BaseModel):
    refresh_token: str

class MessageResponse(BaseModel):
    message: str

class TokenRequest(BaseModel):
    token: str = Field(min_length = 1, max_length = 512)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length = 1, max_length = 512)
    new_password: str = Field(min_length = 8, max_length = 128)

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length = 8, max_length = 128)

class DeleteAccountRequest(BaseModel):
    password: str
