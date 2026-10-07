from datetime import datetime, timedelta, timezone
from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.security import create_access_token, create_refresh_token, hash_password, hash_token, verify_password
from app.models.auth import RefreshRequest, TokenResponse, UserCreateRequest, UserLoginRequest, UserResponse
from app.services.email_service import EmailService
from app.services.login_throttle import LoginThrottle

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"
_TOKEN_LIFETIMES = {VERIFY_EMAIL: timedelta(hours=48), RESET_PASSWORD: timedelta(hours=1)}
# Minimum seconds between verification / reset emails to one address.
EMAIL_COOLDOWN_SECONDS = 60


class AuthService:
    def __init__(
        self,
        database: AsyncIOMotorDatabase,
        settings: Settings,
        redis: Redis | None = None,
        email_service: EmailService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.redis = redis
        self.email_service = email_service or EmailService(settings)
        self.users = database["user"]
        self.refresh_tokens = database["refresh_token"]
        # One-time tokens for email verification and password reset.
        self.user_tokens = database["user_tokens"]
        self.throttle = LoginThrottle(
            redis,
            max_failures=settings.login_max_failures,
            max_failures_per_ip=settings.login_max_failures_per_ip,
            window_seconds=settings.login_lockout_minutes * 60,
        )

    async def initialize(self) -> None:
        await self.users.create_index("email", unique = True)
        await self.refresh_tokens.create_index("token_hash", unique = True)
        await self.refresh_tokens.create_index("expires_at", expireAfterSeconds = 0)
        await self.user_tokens.create_index("token_hash", unique = True)
        await self.user_tokens.create_index("expires_at", expireAfterSeconds = 0)

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
            "email_verified": False,
            "created_at": now
        }

        result = await self.users.insert_one(document)
        document["_id"] = result.inserted_id

        # Starts the resend cooldown too.
        await self._email_cooldown_ok(email, VERIFY_EMAIL)
        await self._send_verification(document)
        return self._to_user_response(document)

    async def login(self, request: UserLoginRequest, client_ip: str | None = None) -> TokenResponse:
        email = request.email.lower()
        await self.throttle.check(email, client_ip)
        user = await self.users.find_one({"email": email})

        if user is None or not verify_password(request.password, user["password_hash"]):
            await self.throttle.record_failure(email, client_ip)
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Invalid email or password"
            )
        await self.throttle.clear(email)

        if not user.get("is_active", False):
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail = "This account is inactive"
            )

        return await self._issue_tokens(str(user["_id"]))

    async def refresh(self, request: RefreshRequest) -> TokenResponse:
        token_hash = hash_token(request.refresh_token)

        stored_token = await self.refresh_tokens.find_one(
            {
                "token_hash": token_hash,
                "revoked_at": None
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
                detail = "User account is unavailable / user not registered."
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

    # ------------------------------------------------- email verification

    async def resend_verification(self, user_id: str) -> None:
        user = await self._get_user_document(user_id)
        if user.get("email_verified"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your email address is already verified")
        if not await self._email_cooldown_ok(user["email"], VERIFY_EMAIL):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "A verification email was sent recently; please wait a minute",
            )
        await self._send_verification(user)

    async def verify_email(self, token: str) -> None:
        record = await self._consume_token(token, VERIFY_EMAIL)
        await self.users.update_one(
            {"_id": ObjectId(record["user_id"])},
            {"$set": {"email_verified": True, "email_verified_at": datetime.now(timezone.utc)}},
        )

    async def _send_verification(self, user: dict) -> None:
        token = await self._issue_one_time_token(str(user["_id"]), VERIFY_EMAIL)
        link = self.email_service.link("/verify-email", token)
        await self.email_service.send(
            user["email"],
            "Verify your email address",
            f"Confirm this address for your LLM-DBMS account:\n\n{link}\n\n"
            "The link expires in 48 hours. If you didn't create an account, ignore this email.",
        )

    # ------------------------------------------------- passwords

    async def request_password_reset(self, email: str) -> None:
        """Always succeeds from the caller's point of view, so it can't reveal which emails exist."""
        user = await self.users.find_one({"email": email.lower(), "is_active": True})
        if user is None or not await self._email_cooldown_ok(user["email"], RESET_PASSWORD):
            return
        token = await self._issue_one_time_token(str(user["_id"]), RESET_PASSWORD)
        link = self.email_service.link("/reset-password", token)
        await self.email_service.send(
            user["email"],
            "Reset your password",
            f"Someone asked to reset the password for your LLM-DBMS account.\n\n{link}\n\n"
            "The link expires in 1 hour. If it wasn't you, ignore this email; your password is unchanged.",
        )

    async def reset_password(self, token: str, new_password: str) -> None:
        record = await self._consume_token(token, RESET_PASSWORD)
        user = await self._set_password(record["user_id"], new_password)
        # Whoever holds the reset link controls the inbox, which also proves the address.
        await self.users.update_one({"_id": user["_id"]}, {"$set": {"email_verified": True}})
        await self.throttle.clear(user["email"])

    async def change_password(self, user_id: str, current_password: str, new_password: str) -> TokenResponse:
        user = await self._get_user_document(user_id)
        if not verify_password(current_password, user["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect")
        await self._set_password(user_id, new_password)
        # Every other session was signed out; keep this one.
        return await self._issue_tokens(user_id)

    async def _set_password(self, user_id: str, new_password: str) -> dict:
        now = datetime.now(timezone.utc)
        user = await self.users.find_one_and_update(
            {"_id": ObjectId(user_id), "is_active": True},
            {"$set": {"password_hash": hash_password(new_password), "password_changed_at": now}},
        )
        if user is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This account is unavailable")
        await self.revoke_all_refresh_tokens(user_id)
        return user

    async def revoke_all_refresh_tokens(self, user_id: str) -> None:
        await self.refresh_tokens.update_many(
            {"user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(timezone.utc)}},
        )

    # ------------------------------------------------- account removal

    async def verify_password_for(self, user_id: str, password: str) -> None:
        user = await self._get_user_document(user_id)
        if not verify_password(password, user["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your password is incorrect")

    async def delete_user(self, user_id: str) -> None:
        await self.refresh_tokens.delete_many({"user_id": user_id})
        await self.user_tokens.delete_many({"user_id": user_id})
        await self.users.delete_one({"_id": ObjectId(user_id)})

    # ------------------------------------------------- one-time tokens

    async def _issue_one_time_token(self, user_id: str, purpose: str) -> str:
        raw, token_hash = create_refresh_token()
        # A new link replaces any earlier unused one for the same purpose.
        await self.user_tokens.delete_many({"user_id": user_id, "purpose": purpose})
        await self.user_tokens.insert_one({
            "user_id": user_id,
            "purpose": purpose,
            "token_hash": token_hash,
            "expires_at": datetime.now(timezone.utc) + _TOKEN_LIFETIMES[purpose],
        })
        return raw

    async def _consume_token(self, token: str, purpose: str) -> dict:
        record = await self.user_tokens.find_one_and_delete(
            {"token_hash": hash_token(token), "purpose": purpose}
        )
        if record is None or record["expires_at"] <= datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired")
        return record

    async def _email_cooldown_ok(self, email: str, purpose: str) -> bool:
        if self.redis is None:
            return True
        try:
            return bool(await self.redis.set(f"emailcooldown:{purpose}:{email}", 1, ex=EMAIL_COOLDOWN_SECONDS, nx=True))
        except Exception:
            return True

    async def _get_user_document(self, user_id: str) -> dict:
        user = None
        if ObjectId.is_valid(user_id):
            user = await self.users.find_one({"_id": ObjectId(user_id), "is_active": True})
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        return user

    async def get_user_for_token(self, user_id: str, issued_at: int | None) -> UserResponse:
        """
        The user an access token names, refusing tokens issued before the
        user's last password change (so a reset signs out stolen sessions).
        """
        user = await self.get_user(user_id)
        changed_at = (await self.users.find_one(
            {"_id": ObjectId(user_id)}, {"password_changed_at": 1}
        ) or {}).get("password_changed_at")
        if changed_at is not None and issued_at is not None and issued_at < int(changed_at.timestamp()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your password was changed; please sign in again",
            )
        return user

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
            email_verified=document.get("email_verified", False),
            created_at=document["created_at"],
        )