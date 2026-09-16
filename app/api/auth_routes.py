import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.rate_limit import rate_limit_auth, rate_limit_login, rate_limit_reset
from app.api.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageOut,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserOut,
)
from app.db.database import get_db
from app.db.models import User
from app.services import auth_service, email_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# What the forgot-password route says, whatever happened. Saying anything more
# specific turns it into a way of asking which addresses have accounts.
RESET_SENT = (
    "If that address has an account, a code is on its way. It's good for "
    f"{auth_service.RESET_CODE_TTL_MINUTES} minutes."
)


def _tokens(db: Session, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=auth_service.create_access_token(user.id),
        refresh_token=auth_service.create_refresh_token(db, user.id),
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create an account. Usernames and emails are unique; display names are not."""
    try:
        user = auth_service.create_user(
            db,
            username=payload.username,
            email=str(payload.email).strip().lower(),
            password=payload.password,
            display_name=(payload.display_name or "").strip() or payload.username,
        )
    except IntegrityError:
        # Let the unique indexes be the arbiter rather than a check-then-insert,
        # which two simultaneous registrations could both pass.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username or email is already taken",
        )

    logger.info(f"Registered {user.username} (id={user.id})")
    return _tokens(db, user)


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_login)]
)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Sign in with a username or an email address."""
    user = auth_service.authenticate_user(db, payload.identifier, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return _tokens(db, user)


@router.post(
    "/refresh", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)]
)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    user_id = auth_service.verify_refresh_token(db, payload.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    # Rotate: the presented token is spent.
    auth_service.revoke_refresh_token(db, payload.refresh_token)
    return _tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    auth_service.revoke_refresh_token(db, payload.refresh_token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name cannot be blank",
            )
        current_user.display_name = display_name
    if payload.bio is not None:
        current_user.bio = payload.bio.strip() or None
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the password. Every existing session is logged out."""
    if not auth_service.verify_password(
        payload.current_password, current_user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    auth_service.change_password(db, current_user, payload.new_password)


@router.post(
    "/forgot-password",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_reset)],
)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Send a code to an address, if that address has an account.

    Always the same answer, and always at the same speed — the send happens on
    a background thread, so an address that exists doesn't take measurably
    longer than one that doesn't.
    """
    user = auth_service.get_user_by_email(db, str(payload.email))
    if user is None:
        logger.info("Password reset asked for an address with no account")
        return MessageOut(detail=RESET_SENT)

    if not email_service.configured():
        # Worth shouting about: from the outside this is indistinguishable
        # from a working reset, and the person will simply never get an email.
        logger.error(
            "Password reset requested but SMTP is not configured — "
            "set SMTP_USER and SMTP_PASSWORD"
        )
        return MessageOut(detail=RESET_SENT)

    code = auth_service.issue_reset_code(db, user)
    email_service.send_password_reset(
        user.email,
        user.display_name,
        code,
        auth_service.RESET_CODE_TTL_MINUTES,
    )
    logger.info(f"Issued a reset code for user {user.id}")
    return MessageOut(detail=RESET_SENT)


@router.post(
    "/reset-password",
    response_model=MessageOut,
    dependencies=[Depends(rate_limit_login)],
)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Spend a reset code and set a new password. Every session is logged out."""
    user = auth_service.get_user_by_email(db, str(payload.email))
    # One message for a wrong address, a wrong code and an expired code alike:
    # telling them apart is telling somebody which of the three they got right.
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="That code isn't right, or it has expired. Ask for a new one.",
    )
    if user is None or not auth_service.redeem_reset_code(db, user, payload.code):
        raise invalid

    auth_service.change_password(db, user, payload.new_password)
    logger.info(f"Password reset completed for user {user.id}")
    return MessageOut(detail="Password changed. Sign in with the new one.")
