from datetime import datetime, timedelta, timezone
from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import Settings
from app.core.security import create_access_token, create_refresh_token, hash_password, hash_token, verify_password
from app.models.auth import RefreshRequest, TokenResponse, UserCreateRequest, UserLoginRequest, UserResponse

class AuthService:
    def __init__(self, database: AsyncIOMotorDatabase, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.users = database["user"]
        self.refresh_tokens = database["refresh_token"]

    async def initialize(self) -> None:
        await self.users.create_index("email", unique = True)
        await self.refresh_tokens.create_index("token_hash", unique = True)
        await self.refresh_tokens.create_index("expires_at", expireAfterSeconds = 0)

    async def register(self, request: UserCreateRequest) -> UserResponse:
        email = request.email.lower()

        existing_user = await self.users.find_one({"email": email})
        if existing_user is not None:
            raise HTTPException(status_code = status.HTTP_409_CONFLICT,
                                detail = "An account with this email already exists")

        now = datetime.now(timezone.utc)
        document = {
            "email": email,
            "password_hash": hash_password(request.password),
            "is_active": True,
            "created_at": now
        }

        result = await self.users.insert_one(document)
        document["_id"] = result.inserted_id

        return self._to_user_response(document)

    async def login(self, request: UserLoginRequest) -> TokenResponse:
        email = request.email.lower()
        user = await self.users.find_one({"email": email})

        if user is None or not verify_password(request.password, user["password_hash"]):
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Invalid email or password"
            )

        if not user.get("is_active", False):
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail = "This account is inacitve"
            )

        return await self._issue_tokens(str(user["_id"]))

    async def refresh(self, request: RefreshRequest) -> TokenResponse:
        token_hash = hash_token(request.refresh_token)

        stored_token = await self.refresh_tokens.find_one(
            {
                "token_hash": token_hash,
                "revoked-at": None
            }
        )

        if stored_token is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Invalid refresh token"
            )

        if stored_token["expires_at"] <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Refresh token has expired"
            )

        await self.refresh_tokens.update_one(
            {"_id": stored_token["_id"]},
            {
                "$set": {
                    "revoked_at": datetime.now(timezone.utc)
                }
            }
        )

        user_id = stored_token["user_id"]
        user = await self.users.find_one(
            {
                "_id": ObjectId(user_id),
                "is_active": True
            }
        )

        if user is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "User account is unavailable / user not registerd."
            )

        return await self._issue_tokens(user_id)

    async def revoke_refresh_token(self, request: RefreshRequest) -> None:
        await self.refresh_tokens.update_one(
            {
                "token_hash": hash_token(request.refresh_token),
                "revoked_at": None
            },
            {
                "$set": {
                    "revoked_at": datetime.now(timezone.utc)
                }
            }
        )

    async def get_user(self, user_id: str) -> UserResponse:
        if not ObjectId.is_valid(user_id):
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Invalid user identifier"
            )

        user = await self.users.find_one(
            {
                "_id": ObjectId(user_id),
                "is_active": True
            }
        )

        if user is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "User not found"
            )

        return self._to_user_response(user)

    async def _issue_tokens(self, user_id: str) -> TokenResponse:
        access_token, expires_in = create_access_token(subject = user_id, settings = self.settings)
        refresh_token, refresh_token_hash = create_refresh_token()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self.settings.refresh_token_expire_days,
        )

        await self.refresh_tokens.insert_one(
            {
                "user_id": user_id,
                "token_hash": refresh_token_hash,
                "expires_at": expires_at,
                "revoked_at": None,
                "created_at": datetime.now(timezone.utc),
            }
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    @staticmethod
    def _to_user_response(document: dict) -> UserResponse:
        return UserResponse(
            id=str(document["_id"]),
            email=document["email"],
            is_active=document["is_active"],
            created_at=document["created_at"],
        )