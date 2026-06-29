"""Operate-platform coupon integration."""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from backend.config import get_settings
from backend.schemas.billing import CouponResponse

logger = logging.getLogger(__name__)


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _amount_to_cents(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return max(0, int((Decimal(str(value)) * Decimal("100")).to_integral_value()))
    except (InvalidOperation, ValueError):
        return 0


def _status_label(status: int | None, valid_until: datetime | None) -> str:
    if status == 2:
        return "已使用"
    if status == 3:
        return "已过期"
    if valid_until and valid_until < datetime.now(valid_until.tzinfo):
        return "已过期"
    if status == 1:
        return "未使用"
    if status == 0:
        return "未兑换"
    return "未知"


def _normalize_coupon(row: dict[str, Any]) -> CouponResponse | None:
    coupon_id = row.get("id")
    if coupon_id is None:
        return None
    try:
        coupon_id = int(coupon_id)
    except (TypeError, ValueError):
        return None

    raw_status = row.get("usageStatus", row.get("usage_status"))
    try:
        raw_status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        raw_status = None

    valid_until = _parse_date(row.get("invalidTime") or row.get("invalid_time"))
    amount_cents = _amount_to_cents(row.get("denomination"))
    return CouponResponse(
        id=coupon_id,
        code=row.get("code"),
        amount_cents=amount_cents,
        amount_yuan=amount_cents / 100,
        valid_until=valid_until,
        status=_status_label(raw_status, valid_until),
        raw_status=raw_status,
    )


async def list_user_coupons(customer_account: str, *, include_all: bool = True) -> list[CouponResponse]:
    """Fetch coupons from operate-two d_coupon open endpoints.

    On integration failure we return an empty list. Coupon lookup must not break
    the core AI check flow.
    """
    settings = get_settings()
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        return []
    path = "/system/coupon/openAllList" if include_all else "/system/coupon/openList"
    params = {"customerAccount": customer_account, "pageNum": 1, "pageSize": 1000}
    try:
        async with httpx.AsyncClient(timeout=settings.operate_api_timeout_seconds, trust_env=False) as client:
            response = await client.get(f"{base_url}{path}", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("[operate-coupons] list failed for %s: %s", customer_account, exc)
        return []

    rows = data.get("rows") if isinstance(data, dict) else None
    if rows is None and isinstance(data, dict):
        rows = data.get("data", {}).get("rows") if isinstance(data.get("data"), dict) else None
    if not isinstance(rows, list):
        return []

    coupons: list[CouponResponse] = []
    for row in rows:
        if isinstance(row, dict):
            normalized = _normalize_coupon(row)
            if normalized:
                coupons.append(normalized)
    return coupons


async def find_available_coupon(customer_account: str, coupon_id: int) -> CouponResponse | None:
    coupons = await list_user_coupons(customer_account, include_all=True)
    for coupon in coupons:
        if coupon.id == coupon_id and coupon.status == "未使用" and coupon.amount_cents > 0:
            return coupon
    return None


async def mark_coupon_used(coupon_id: int) -> bool:
    settings = get_settings()
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=settings.operate_api_timeout_seconds, trust_env=False) as client:
            response = await client.get(
                f"{base_url}/system/coupon/exchangeCoupon",
                params={"id": coupon_id},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("[operate-coupons] mark-used failed for %s: %s", coupon_id, exc)
        return False
    code = data.get("code") if isinstance(data, dict) else None
    return code in (0, 200, "0", "200")
