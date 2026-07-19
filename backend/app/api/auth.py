from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    LoginRequest,
    QuotaOut,
    RegisterRequest,
    SendCodeRequest,
    SendCodeResponse,
    TokenResponse,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.auth_codes import (
    consume_code,
    create_and_send_code,
    detect_channel,
    normalize_email,
    normalize_phone,
    normalize_target,
)
from app.services.quotas import quota_status

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/send-code", response_model=SendCodeResponse)
def send_code(body: SendCodeRequest, db: Session = Depends(get_db)) -> SendCodeResponse:
    settings = get_settings()
    try:
        channel = body.channel
        # allow auto-detect if user typed wrong channel label but right format
        detected = detect_channel(body.target)
        if detected != channel:
            channel = detected
        if channel == "phone" and not settings.auth_allow_phone_register:
            raise ValueError("一期仅支持邮箱注册；短信通道尚未接入")
        target = normalize_target(channel, body.target)

        if channel == "email":
            exists = db.scalar(select(User).where(User.email == target))
            if exists:
                raise HTTPException(status_code=400, detail="该邮箱已注册")
        else:
            exists = db.scalar(select(User).where(User.phone == target))
            if exists:
                raise HTTPException(status_code=400, detail="该手机号已注册")

        payload = create_and_send_code(db, channel=channel, target=target, purpose="register")
        return SendCodeResponse(**payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    username = body.username.strip()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")

    if body.channel == "phone" and not settings.auth_allow_phone_register:
        raise HTTPException(status_code=400, detail="一期仅支持邮箱注册；短信通道尚未接入")

    email = normalize_email(body.email) if body.channel == "email" and body.email else None
    phone = normalize_phone(body.phone) if body.channel == "phone" and body.phone else None
    contact = email or phone
    if not contact:
        raise HTTPException(status_code=400, detail="请提供邮箱")

    try:
        consume_code(db, channel=body.channel, target=contact, code=body.code, purpose="register")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if email and db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=400, detail="该邮箱已注册")
    if phone and db.scalar(select(User).where(User.phone == phone)):
        raise HTTPException(status_code=400, detail="该手机号已注册")

    user = User(
        username=username,
        email=email,
        phone=phone,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, extra={"username": user.username})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    account = body.account.strip()
    user = None
    try:
        channel = detect_channel(account)
        target = normalize_target(channel, account)
        if channel == "email":
            user = db.scalar(select(User).where(User.email == target))
        else:
            user = db.scalar(select(User).where(User.phone == target))
    except ValueError:
        # also allow username login as convenience for existing accounts
        user = db.scalar(select(User).where(User.username == account))

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    token = create_access_token(user.id, extra={"username": user.username})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    q = quota_status(db, user.id)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        phone=user.phone,
        created_at=user.created_at,
        quota=QuotaOut(**q),
    )
