"""Auth API — registration, login, logout, API keys, admin user management, session leasing."""

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from config import settings as _cfg

# Cookie secure flag — True when accessed over HTTPS (direct or via reverse proxy)
def _cookie_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "") == "https"

from core.auth import (
    hash_password, verify_password, create_access_token,
    create_api_key, get_current_user, get_admin_user, get_optional_user, get_db,
)
from config import settings
from models.user import User, ApiKey

router = APIRouter(tags=["auth"])


# ─── Request models ───

class RegisterRequest(BaseModel):
    username: str
    email: str | None = None
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ApiKeyCreate(BaseModel):
    name: str


class AdminUserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None


# ─── Auth endpoints ───

@router.post("/auth/register")
async def register(request: Request, req: RegisterRequest, db=Depends(get_db)):
    """Create a new account."""
    if len(req.username) < 2:
        raise HTTPException(400, "Username too short")
    if len(req.password) < 4:
        raise HTTPException(400, "Password too short")

    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Username already taken")

    if req.email:
        existing_email = await db.execute(select(User).where(User.email == req.email))
        if existing_email.scalar_one_or_none():
            raise HTTPException(409, "Email already registered")

    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user)
    response = Response(
        content=json.dumps({"user": user.to_dict(), "ok": True}),
        media_type="application/json",
        status_code=200,
    )
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.jwt_expire_hours * 3600,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return response


@router.post("/auth/login")
async def login(request: Request, req: LoginRequest, db=Depends(get_db)):
    """Login and set JWT cookie."""
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    token = create_access_token(user)

    response = Response(
        content='{"ok": true}',
        media_type="application/json",
        status_code=200,
    )
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.jwt_expire_hours * 3600,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return response


@router.post("/auth/logout")
async def logout():
    """Clear JWT cookie."""
    from fastapi import Response as Resp
    response = Resp(content='{"ok": true}', media_type="application/json")
    response.delete_cookie(key="access_token")
    return response


@router.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    """Get current user info."""
    return user.to_dict()


# ─── API Keys ───

@router.post("/auth/keys")
async def create_key(req: ApiKeyCreate, user: User = Depends(get_current_user), db=Depends(get_db)):
    """Create an API key. The raw key is shown only once."""
    raw_key, api_key_obj = await create_api_key(user.id, req.name, db)
    return {
        "id": api_key_obj.id,
        "name": api_key_obj.name,
        "key": raw_key,
        "key_prefix": api_key_obj.key_prefix,
        "created_at": api_key_obj.created_at.isoformat() if api_key_obj.created_at else None,
    }


@router.get("/auth/keys")
async def list_keys(user: User = Depends(get_current_user), db=Depends(get_db)):
    """List user's API keys (prefix only, no secret)."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        {
            "id": k.id,
            "name": k.name,
            "key_prefix": k.key_prefix,
            "last_used": k.last_used.isoformat() if k.last_used else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        }
        for k in keys
    ]


@router.delete("/auth/keys/{key_id}")
async def delete_key(key_id: int, user: User = Depends(get_current_user), db=Depends(get_db)):
    """Revoke an API key."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(404, "Key not found")
    await db.delete(key)
    await db.commit()
    return {"ok": True}


# ─── Admin ───

@router.get("/auth/admin/users")
async def admin_list_users(admin: User = Depends(get_admin_user), db=Depends(get_db)):
    """List all users (admin only)."""
    result = await db.execute(select(User).order_by(User.id))
    return [u.to_dict() for u in result.scalars().all()]


@router.patch("/auth/admin/users/{user_id}")
async def admin_update_user(user_id: int, req: AdminUserUpdate, admin: User = Depends(get_admin_user), db=Depends(get_db)):
    """Update user role/active status (admin only)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if req.role is not None:
        if req.role not in ("user", "admin"):
            raise HTTPException(400, "Invalid role")
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    await db.commit()
    return user.to_dict()


@router.delete("/auth/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: User = Depends(get_admin_user), db=Depends(get_db)):
    """Delete a user (admin only)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    await db.delete(user)
    await db.commit()
    return {"ok": True}


# ─── Session Leasing (bootstrap token auth) ───

class SessionAcquireRequest(BaseModel):
    name: str = ""  # optional label like "rog-2" or "lappy-1"


@router.post("/auth/session/acquire")
async def acquire_session(req: Request, body: SessionAcquireRequest, db=Depends(get_db)):
    """Create an ephemeral user + API key for a Claude Code instance.
    Requires X-Bootstrap-Token header matching ALPHABETTY_BOOTSTRAP_TOKEN.
    """
    token = req.headers.get("x-bootstrap-token", "")
    if not token or token != settings.bootstrap_token:
        raise HTTPException(401, "Invalid bootstrap token")

    name = body.name or secrets.token_hex(4)
    username = f"session-{name}-{secrets.token_hex(3)}"

    user = User(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    raw_key, api_key_obj = await create_api_key(user.id, f"session-{name}", db)
    return {
        "user_id": user.id,
        "username": user.username,
        "api_key": raw_key,
    }


@router.post("/auth/session/release")
async def release_session(user: User = Depends(get_current_user), db=Depends(get_db)):
    """Release own session — deletes the calling user and their API keys.
    Only works for session-* users (ephemeral).
    """
    if not user.username.startswith("session-"):
        raise HTTPException(400, "Not an ephemeral session")
    await db.delete(user)
    await db.commit()
    return {"ok": True, "released": user.id}
