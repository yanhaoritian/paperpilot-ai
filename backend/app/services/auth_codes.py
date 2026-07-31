from __future__ import annotations

import logging
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthCode
from app.security import hash_password, verify_password

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^1\d{10}$")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    return re.sub(r"[\s\-]", "", value.strip())


def detect_channel(target: str) -> str:
    t = target.strip()
    if EMAIL_RE.match(t):
        return "email"
    phone = normalize_phone(t)
    if PHONE_RE.match(phone):
        return "phone"
    raise ValueError("请输入有效的邮箱或中国大陆手机号")


def normalize_target(channel: str, target: str) -> str:
    if channel == "email":
        return normalize_email(target)
    return normalize_phone(target)


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _deliver_code(channel: str, target: str, code: str) -> str:
    """Deliver verification code. Returns delivery mode: smtp | log."""
    settings = get_settings()
    if channel == "phone":
        if not settings.auth_allow_phone_register:
            raise ValueError("一期仅支持邮箱注册；短信通道尚未接入")
        if not settings.auth_expose_code:
            raise ValueError("手机验证码需配置短信网关；当前请使用邮箱注册")
        logger.info("auth_code channel=phone target=%s code=%s", target, code)
        return "log"

    if channel == "email":
        if settings.smtp_configured:
            msg = EmailMessage()
            msg["Subject"] = "PaperPilot 注册验证码"
            msg["From"] = settings.smtp_from or settings.smtp_user
            msg["To"] = target
            msg.set_content(
                f"您的验证码是 {code}，{settings.auth_code_ttl_seconds // 60} 分钟内有效。"
            )
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
            return "smtp"
        if settings.auth_expose_code:
            logger.info("auth_code channel=email target=%s code=%s", target, code)
            return "log"
        raise ValueError("未配置 SMTP，无法发送邮箱验证码。请在 .env 中配置 SMTP_*")

    raise ValueError("不支持的验证渠道")


def create_and_send_code(db: Session, *, channel: str, target: str, purpose: str = "register") -> dict:
    settings = get_settings()
    if channel == "phone" and not settings.auth_allow_phone_register:
        raise ValueError("一期仅支持邮箱注册；短信通道尚未接入")

    target_n = normalize_target(channel, target)
    now = datetime.now(timezone.utc)

    latest = db.scalar(
        select(AuthCode)
        .where(
            AuthCode.channel == channel,
            AuthCode.target == target_n,
            AuthCode.purpose == purpose,
            AuthCode.used == 0,
        )
        .order_by(AuthCode.created_at.desc())
    )
    if latest and latest.created_at:
        created = latest.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (now - created).total_seconds() < settings.auth_code_cooldown_seconds:
            wait = int(settings.auth_code_cooldown_seconds - (now - created).total_seconds())
            raise ValueError(f"发送过于频繁，请 {wait} 秒后再试")

    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    sent_today = db.scalars(
        select(AuthCode).where(
            AuthCode.channel == channel,
            AuthCode.target == target_n,
            AuthCode.purpose == purpose,
            AuthCode.created_at >= day_start,
        )
    ).all()
    daily_max = int(settings.auth_code_daily_max_per_target or 0)
    if daily_max > 0 and len(sent_today) >= daily_max:
        raise ValueError(f"该邮箱今日验证码次数已达上限（{daily_max} 次），请明天再试")

    code = generate_code()
    row = AuthCode(
        channel=channel,
        target=target_n,
        code_hash=hash_password(code),
        purpose=purpose,
        expires_at=now + timedelta(seconds=settings.auth_code_ttl_seconds),
        used=0,
    )
    db.add(row)
    db.commit()

    mode = _deliver_code(channel, target_n, code)
    payload = {
        "ok": True,
        "channel": channel,
        "target": target_n,
        "delivery": mode,
        "expires_in": settings.auth_code_ttl_seconds,
        "message": "验证码已发送到邮箱" if mode == "smtp" else "验证码已生成（开发模式见返回码或服务端日志）",
    }
    if settings.auth_expose_code:
        payload["dev_code"] = code
    return payload


def consume_code(db: Session, *, channel: str, target: str, code: str, purpose: str = "register") -> None:
    target_n = normalize_target(channel, target)
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(AuthCode)
        .where(
            AuthCode.channel == channel,
            AuthCode.target == target_n,
            AuthCode.purpose == purpose,
            AuthCode.used == 0,
        )
        .order_by(AuthCode.created_at.desc())
        .limit(5)
    ).all()
    for row in rows:
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            continue
        if verify_password(code.strip(), row.code_hash):
            row.used = 1
            db.commit()
            return
    raise ValueError("验证码无效或已过期")
