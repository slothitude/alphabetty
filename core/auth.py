"""Central auth module — password hashing, JWT, API keys, FastAPI dependencies."""

import hashlib
import secrets
from datetime import datetime, timezone, timedelta

import jwt
import bcrypt
from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.user import User, ApiKey


# ─── Password hashing ───

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ─── JWT ───

def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


async def _validate_jwt(token: str, db: AsyncSession) -> User | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = int(payload.get("sub", 0))
    except (jwt.InvalidTokenError, ValueError):
        return None
    result = await db.get(User, user_id)
    if result and result.is_active:
        return result
    return None


# ─── API Keys ───

def _hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(user_id: int, name: str, db: AsyncSession) -> tuple[str, ApiKey]:
    """Generate an API key, store its hash. Returns (raw_key, api_key_obj). Raw key shown once."""
    raw_key = f"alph_{secrets.token_urlsafe(32)}"
    key_hash = _hash_api_key(raw_key)
    key_prefix = raw_key[:12]

    api_key = ApiKey(user_id=user_id, name=name, key_hash=key_hash, key_prefix=key_prefix)
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return raw_key, api_key


async def _validate_api_key(raw_key: str, db: AsyncSession) -> User | None:
    key_hash = _hash_api_key(raw_key)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    if not api_key:
        return None
    api_key.last_used = datetime.now(timezone.utc)
    await db.commit()
    user = await db.get(User, api_key.user_id)
    if user and user.is_active:
        return user
    return None


# ─── FastAPI Dependencies ───

def _extract_token(request: Request) -> str | None:
    """Extract JWT from HttpOnly cookie."""
    return request.cookies.get("access_token")


def _extract_bearer(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


async def get_db():
    """Shared DB session dependency for auth."""
    from app import async_session
    async with async_session() as session:
        yield session


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Dependency: returns authenticated User or raises 401.

    Tries Bearer header first (API key or JWT), then HttpOnly cookie (JWT).
    """
    # 1. Bearer header
    bearer = _extract_bearer(request)
    if bearer:
        # Try as API key first (alph_ prefix)
        if bearer.startswith("alph_"):
            user = await _validate_api_key(bearer, db)
            if user:
                return user
        # Try as JWT
        user = await _validate_jwt(bearer, db)
        if user:
            return user

    # 2. Cookie
    token = _extract_token(request)
    if token:
        user = await _validate_jwt(token, db)
        if user:
            return user

    raise HTTPException(status_code=401, detail="Not authenticated")


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Dependency: returns User if admin, else 403."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    """Dependency: returns User if authenticated, None otherwise. For shared resources."""
    # Bearer
    bearer = _extract_bearer(request)
    if bearer:
        if bearer.startswith("alph_"):
            user = await _validate_api_key(bearer, db)
            if user:
                return user
        user = await _validate_jwt(bearer, db)
        if user:
            return user
    # Cookie
    token = _extract_token(request)
    if token:
        user = await _validate_jwt(token, db)
        if user:
            return user
    return None
