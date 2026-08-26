"""前端能力开关接口：登录页/布局据此隐藏公有云专属功能。

私有云模式（billing_mode=private_cloud）下：
- 短信注册/重置、充值缴费、优惠券 —— 全部禁用；
- 钱包面板改显"AI/OCR 剩余次数"。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import get_settings
from backend.services.quota_client import is_private_cloud

router = APIRouter(tags=["config"])


class FeaturesResponse(BaseModel):
    billing_mode: str
    private_cloud: bool
    recharge_enabled: bool
    sms_enabled: bool
    register_enabled: bool
    coupon_enabled: bool


@router.get("/config/features", response_model=FeaturesResponse)
async def get_features() -> FeaturesResponse:
    private_cloud = is_private_cloud()
    return FeaturesResponse(
        billing_mode=get_settings().billing_mode,
        private_cloud=private_cloud,
        recharge_enabled=not private_cloud,
        sms_enabled=not private_cloud,
        register_enabled=not private_cloud,
        coupon_enabled=not private_cloud,
    )
