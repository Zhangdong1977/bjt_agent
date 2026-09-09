"""Machine-to-machine AI编标 admin API for operate-two（M2 决策 36，2026-09-09）.

- 开关 DB 化：PUT /admin/bid-wizard/settings 推送 mode（即时生效，每请求直读）；
- 白名单管理：GET/POST/DELETE /admin/bid-wizard/whitelist，支持手机号/用户名/
  运营台 user_id（external_user_id）解析，运营台直连不建镜像表。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.api.admin import verify_internal_key
from backend.api.deps import DBSession
from backend.models import BidWizardSetting, BidWizardWhitelist, User


router = APIRouter(
    prefix="/admin/bid-wizard",
    tags=["Admin Bid Wizard"],
    dependencies=[Depends(verify_internal_key)],
)


class BidWizardSettingsPayload(BaseModel):
    mode: Literal["enabled", "whitelist", "disabled"]


class BidWizardWhitelistAddPayload(BaseModel):
    # 手机号 / 用户名 / 运营台 sys_user.user_id（数字字符串）
    identifier: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)


@router.get("/settings")
async def get_settings(db: DBSession):
    row = (
        await db.execute(select(BidWizardSetting).where(BidWizardSetting.id == "default"))
    ).scalar_one_or_none()
    if row is None:
        return {"mode": None, "source": "env"}
    return {"mode": row.mode, "source": "db", "updated_at": row.updated_at}


@router.put("/settings")
async def put_settings(payload: BidWizardSettingsPayload, db: DBSession):
    """运营台推送开关模式（决策 36）：upsert 单行 default；每请求直读、即时生效。"""
    row = (
        await db.execute(select(BidWizardSetting).where(BidWizardSetting.id == "default"))
    ).scalar_one_or_none()
    if row is None:
        row = BidWizardSetting(id="default", mode=payload.mode)
        db.add(row)
    else:
        row.mode = payload.mode
    await db.commit()
    await db.refresh(row)
    return {"mode": row.mode, "effective": True}


@router.get("/whitelist")
async def list_whitelist(db: DBSession):
    rows = (
        await db.execute(
            select(BidWizardWhitelist, User)
            .outerjoin(User, User.id == BidWizardWhitelist.user_id)
            .order_by(BidWizardWhitelist.user_id)
        )
    ).all()
    return {
        "items": [
            {
                "user_id": whitelist.user_id,
                "username": user.username if user else None,
                "nickname": user.nickname if user else None,
                "external_user_id": user.external_user_id if user else None,
                "note": whitelist.note,
                "created_at": whitelist.created_at,
            }
            for whitelist, user in rows
        ]
    }


async def _resolve_user(db: DBSession, identifier: str) -> User:
    """identifier 依次按 username 精确 → 运营台 user_id（数字）→ username 前缀匹配。"""
    user = (
        await db.execute(select(User).where(User.username == identifier))
    ).scalar_one_or_none()
    if user is not None:
        return user
    if identifier.isdigit():
        user = (
            await db.execute(
                select(User).where(User.external_user_id == int(identifier))
            )
        ).scalar_one_or_none()
        if user is not None:
            return user
    user = (
        await db.execute(select(User).where(User.username.ilike(f"{identifier}%")))
    ).scalar_one_or_none()
    if user is not None:
        return user
    raise HTTPException(status_code=404, detail=f"未找到用户：{identifier}")


@router.post("/whitelist")
async def add_whitelist(payload: BidWizardWhitelistAddPayload, db: DBSession):
    user = await _resolve_user(db, payload.identifier.strip())
    existing = (
        await db.execute(
            select(BidWizardWhitelist).where(BidWizardWhitelist.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(BidWizardWhitelist(user_id=user.id, note=payload.note))
        await db.commit()
    elif payload.note is not None:
        existing.note = payload.note
        await db.commit()
    return {
        "user_id": user.id,
        "username": user.username,
        "external_user_id": user.external_user_id,
    }


@router.delete("/whitelist/{user_id}")
async def remove_whitelist(user_id: str, db: DBSession):
    row = (
        await db.execute(
            select(BidWizardWhitelist).where(BidWizardWhitelist.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return {"user_id": user_id, "removed": row is not None}
