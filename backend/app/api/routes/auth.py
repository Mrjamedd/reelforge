import random
import string
from datetime import timedelta, timezone, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.models import AdminUser, EmailVerificationToken
from app.schemas.schemas import (
    AdminUserOut,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.services.email_service import send_verification_email

router = APIRouter(prefix="/auth", tags=["auth"])

_VERIFY_TTL_MINUTES = 35


def _generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = AdminUser(
        email=body.email,
        hashed_password=hash_password(body.password),
        is_active=False,
        is_email_verified=False,
    )
    db.add(user)
    await db.flush()

    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_VERIFY_TTL_MINUTES)
    token = EmailVerificationToken(user_id=user.id, token=code, expires_at=expires_at)
    db.add(token)
    await db.commit()

    await send_verification_email(body.email, code)
    return MessageResponse(message="Verification email sent. Check your inbox.")


@router.post("/verify-email", response_model=TokenResponse)
async def verify_email(body: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    user_result = await db.execute(select(AdminUser).where(AdminUser.email == body.email))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    now = datetime.now(timezone.utc)
    token_result = await db.execute(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.token == body.code,
            EmailVerificationToken.used.is_(False),
            EmailVerificationToken.expires_at > now,
        )
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(1)
    )
    token = token_result.scalar_one_or_none()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    token.used = True
    user.is_active = True
    user.is_email_verified = True
    await db.commit()

    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AdminUser).where(AdminUser.email == body.email)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please verify your email first.",
        )

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=AdminUserOut)
async def me(current_user: AdminUser = Depends(get_current_user)):
    return current_user
