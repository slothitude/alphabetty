"""Sign-in REST API — endpoints for auth workflow and credential management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, get_db
from core.signin import workflow
from models.credential import Credential
from models.user import User

router = APIRouter(tags=["signin"])
logger = logging.getLogger(__name__)


# ─── Request models ───

class SignInStartRequest(BaseModel):
    url: str
    username: str
    password: str
    tab_id: Optional[str] = None
    selectors: Optional[dict] = None


class Submit2FARequest(BaseModel):
    code: str
    tab_id: Optional[str] = None


class SaveCredentialRequest(BaseModel):
    name: str
    url: str
    username: str
    password: str
    totp_secret: Optional[str] = None
    selectors: Optional[dict] = None


# ─── Sign-in workflow endpoints ───

@router.post("/signin/start")
async def signin_start(req: SignInStartRequest, user: User = Depends(get_current_user)):
    """Start sign-in flow: navigate, detect form, fill + submit."""
    return await workflow.start(
        url=req.url,
        username=req.username,
        password=req.password,
        tab_id=req.tab_id,
        selectors=req.selectors,
    )


@router.post("/signin/2fa")
async def signin_submit_2fa(req: Submit2FARequest, user: User = Depends(get_current_user)):
    """Submit 2FA code."""
    return await workflow.submit_2fa(req.code, tab_id=req.tab_id)


@router.get("/signin/check-2fa")
async def signin_check_2fa(tab_id: str = None, user: User = Depends(get_current_user)):
    """Check if 2FA is needed on current page."""
    return await workflow.check_2fa(tab_id=tab_id)


@router.get("/signin/status")
async def signin_status(user: User = Depends(get_current_user)):
    """Current sign-in workflow state."""
    return workflow.status()


@router.get("/signin/verify")
async def signin_verify(tab_id: str = None, user: User = Depends(get_current_user)):
    """Verify if currently signed in."""
    return await workflow.verify(tab_id=tab_id)


# ─── Credential CRUD ───

@router.get("/signin/credentials")
async def list_credentials(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List all saved credentials (secrets excluded)."""
    query = select(Credential).order_by(Credential.name)
    if user.role != "admin":
        query = query.where(Credential.user_id == user.id)
    result = await db.execute(query)
    creds = result.scalars().all()
    return [c.to_dict() for c in creds]


@router.post("/signin/credentials")
async def save_credential(req: SaveCredentialRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Save a credential profile."""
    # Check for existing credential with same name for this user
    existing = await db.execute(
        select(Credential).where(Credential.name == req.name, Credential.user_id == user.id)
    )
    cred = existing.scalar_one_or_none()
    if cred:
        # Update existing
        cred.site_url = req.url
        cred.username = req.username
        cred.password = req.password
        cred.totp_secret = req.totp_secret
        cred.selectors = req.selectors
        await db.commit()
        await db.refresh(cred)
        return cred.to_dict(include_secrets=True)
    else:
        cred = Credential(
            name=req.name,
            site_url=req.url,
            username=req.username,
            password=req.password,
            totp_secret=req.totp_secret,
            selectors=req.selectors,
            user_id=user.id,
        )
        db.add(cred)
        await db.commit()
        await db.refresh(cred)
        return cred.to_dict(include_secrets=True)


@router.delete("/signin/credentials/{credential_id}")
async def delete_credential(credential_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Delete a credential."""
    result = await db.execute(
        select(Credential).where(Credential.id == credential_id)
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return {"error": "Not found"}
    if user.role != "admin" and cred.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    await db.delete(cred)
    await db.commit()
    return {"ok": True, "deleted": credential_id}


# ─── Auto sign-in ───

@router.post("/signin/auto/{name}")
async def signin_auto(name: str, tab_id: Optional[str] = None, user: User = Depends(get_current_user)):
    """Auto sign-in using saved credential by name."""
    return await workflow.auto_signin(name, tab_id=tab_id)
