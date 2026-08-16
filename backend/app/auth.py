from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import UUID

from fastapi import Header, HTTPException

from .store import store


SESSION_DAYS = 30
CODE_TTL_MINUTES = 5
CODE_RESEND_SECONDS = 60
DEV_MODE = os.getenv("DIET_AUTH_DEV_MODE", "1") == "1"
IDENTITY_SECRET = os.getenv("DIET_IDENTITY_SECRET", "diet-local-development-secret")
_phone_codes: dict[str, dict[str, Any]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def identity_key(provider: str, value: str) -> str:
    digest = hmac.new(IDENTITY_SECRET.encode("utf-8"), f"{provider}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{provider}:{digest}"


def create_session(user_id: UUID) -> str:
    token = secrets.token_urlsafe(32)
    store.sessions[_token_hash(token)] = {
        "user_id": str(user_id),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(),
    }
    store.persist()
    return token


def session_user(token: str) -> UUID | None:
    session = store.sessions.get(_token_hash(token))
    if not session:
        return None
    if datetime.fromisoformat(session["expires_at"]) <= datetime.now(timezone.utc):
        store.sessions.pop(_token_hash(token), None)
        store.persist()
        return None
    return UUID(session["user_id"])


def bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


def require_user(authorization: str | None = Header(default=None)) -> UUID:
    token = bearer_token(authorization)
    user_id = session_user(token) if token else None
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "登录已失效，请重新登录"})
    return user_id


def optional_user(authorization: str | None = Header(default=None)) -> UUID | None:
    token = bearer_token(authorization)
    return session_user(token) if token else None


def logout_session(authorization: str | None) -> None:
    token = bearer_token(authorization)
    if token:
        store.sessions.pop(_token_hash(token), None)
        store.persist()


def issue_phone_code(phone: str) -> str:
    now = datetime.now(timezone.utc)
    challenge = _phone_codes.get(phone)
    if challenge and challenge["sent_at"] + timedelta(seconds=CODE_RESEND_SECONDS) > now:
        retry_after = int((challenge["sent_at"] + timedelta(seconds=CODE_RESEND_SECONDS) - now).total_seconds()) + 1
        raise HTTPException(
            status_code=429,
            detail={"code": "PHONE_CODE_TOO_FREQUENT", "message": f"请在 {retry_after} 秒后重新获取验证码"},
        )
    code = f"{secrets.randbelow(1_000_000):06d}"
    _phone_codes[phone] = {
        "hash": _token_hash(f"{phone}:{code}"),
        "sent_at": now,
        "expires_at": now + timedelta(minutes=CODE_TTL_MINUTES),
        "attempts": 0,
    }
    return code


def verify_phone_code(phone: str, code: str) -> bool:
    challenge = _phone_codes.get(phone)
    if not challenge or challenge["expires_at"] <= datetime.now(timezone.utc) or challenge["attempts"] >= 5:
        return False
    challenge["attempts"] += 1
    valid = secrets.compare_digest(challenge["hash"], _token_hash(f"{phone}:{code}"))
    if valid:
        _phone_codes.pop(phone, None)
    return valid


def exchange_wechat_code(code: str) -> str:
    if DEV_MODE and code.startswith("dev-wechat-"):
        return code
    app_id = os.getenv("WECHAT_APP_ID")
    app_secret = os.getenv("WECHAT_APP_SECRET")
    if not app_id or not app_secret:
        raise HTTPException(status_code=503, detail={"code": "WECHAT_NOT_CONFIGURED", "message": "微信登录尚未配置 AppID 和 Secret"})
    query = urlencode({"appid": app_id, "secret": app_secret, "js_code": code, "grant_type": "authorization_code"})
    try:
        with urlopen(f"https://api.weixin.qq.com/sns/jscode2session?{query}", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "WECHAT_UNAVAILABLE", "message": "微信授权服务暂时不可用"}) from exc
    openid = payload.get("openid")
    if not openid:
        raise HTTPException(status_code=401, detail={"code": "WECHAT_CODE_INVALID", "message": "微信授权凭证无效，请重试"})
    return openid
