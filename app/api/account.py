"""Account management for the signed-in user."""
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.auth import clear_refresh_cookie, get_auth_service, set_refresh_cookie
from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.core.database import DatabaseManager
from app.models.auth import ChangePasswordRequest, DeleteAccountRequest, MessageResponse, TokenResponse, UserResponse
from app.services.auth import AuthService
from app.services.conversation_service import ConversationService
from app.services.database_registry import DatabaseRegistryService
from app.services.saved_query_service import SavedQueryService
from app.services.usage_service import UsageService

router = APIRouter(prefix="/auth", tags=["account"])


@router.post("/verify-email/resend", response_model=MessageResponse)
async def resend_verification(
    current_user: UserResponse = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    await auth_service.resend_verification(current_user.id)
    return MessageResponse(message=f"We sent a new verification link to {current_user.email}")


@router.post("/password/change", response_model=TokenResponse)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    current_user: UserResponse = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Change the password and sign out every other session; this one gets fresh tokens."""
    tokens = await auth_service.change_password(current_user.id, body.current_password, body.new_password)
    set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    """
    Delete the account, every database it owns (with their data), and its
    access to databases others shared with it.
    """
    await auth_service.verify_password_for(current_user.id, body.password)

    database_manager: DatabaseManager = request.app.state.database_manager
    mongo = database_manager.mongo_database
    registry = DatabaseRegistryService(mongo_database=mongo, database_manager=database_manager)
    for database_id in await registry.owned_database_ids(current_user.id):
        await registry.delete(database_id, current_user.id)
    await registry.remove_grants_to(current_user.id)
    await ConversationService(mongo).delete_user(current_user.id)
    await SavedQueryService(mongo).delete_user(current_user.id)
    await UsageService(mongo).delete_user(current_user.id)
    await auth_service.delete_user(current_user.id)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response)
    return response


@router.get("/me/usage")
async def get_usage(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    """LLM token use for the last 30 days, with the daily limits that apply."""
    settings = get_settings()
    days = await UsageService(request.app.state.database_manager.mongo_database).recent(current_user.id)
    return {
        "days": days,
        "daily_question_limit": settings.ask_daily_limit or None,
        "daily_token_limit": settings.ask_daily_token_limit or None,
    }
