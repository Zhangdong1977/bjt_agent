"""Operate-platform real-payment (交行聚合支付) bridge client.

bjt-agent 的"文"充值借 operate-two 的内部接口走真实交行聚合支付：
- create_recharge_order：调 /bank/pay/bjtCreateOrder 下真实交行单，拿二维码文本 + payMerTranNo。
- query_order_status：调 /bank/pay/bjtOrderStatus（内部主动查交行网关 MPNG020702）取支付状态。

鉴权：shared-secret 头 X-Internal-Token（与 operate-two document.bocom.internalToken 同值）。
不依赖打到生产的异步 notify——主动轮询即可在 dev 取到真实支付结果。
"""

import logging

import httpx
from fastapi import HTTPException, status

from backend.config import get_settings

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    token = get_settings().operate_internal_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="真实支付未配置（OPERATE_INTERNAL_TOKEN 缺失）",
        )
    return {"X-Internal-Token": token}


async def create_recharge_order(
    *, total_amount_yuan: str, package_name: str, external_ref: str
) -> dict[str, str]:
    """创建真实交行聚合支付订单。返回 {display_code_text, pay_mer_tran_no}。失败抛 503。"""
    settings = get_settings()
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="交行下单服务未配置",
        )
    payload = {
        "totalAmount": total_amount_yuan,
        "packageName": package_name,
        "externalRef": external_ref,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.operate_api_timeout_seconds, trust_env=False) as client:
            resp = await client.post(
                f"{base_url}/bank/pay/bjtCreateOrder", json=payload, headers=_headers()
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        logger.error("[operate-recharge] create request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="交行下单服务不可用，请稍后重试",
        ) from exc

    if body.get("code") != 200:
        logger.warning("[operate-recharge] create non-200: %s", body)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=body.get("msg") or "交行下单失败",
        )
    data = body.get("data") or {}
    display_code_text = data.get("displayCodeText")
    pay_mer_tran_no = data.get("payMerTranNo")
    if not display_code_text or not pay_mer_tran_no:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="交行未返回二维码",
        )
    return {"display_code_text": display_code_text, "pay_mer_tran_no": pay_mer_tran_no}


async def query_order_status(pay_mer_tran_no: str) -> str:
    """查询交行订单支付状态，返回 'success' | 'pending' | 'failure'。

    网络/网关瞬时错误降级为 'pending'（避免一次抖动就判失败，前端会继续轮询）。
    """
    settings = get_settings()
    base_url = settings.operate_api_base_url.rstrip("/")
    if not base_url:
        return "pending"
    params = {"payMerTranNo": pay_mer_tran_no}
    try:
        async with httpx.AsyncClient(timeout=settings.operate_api_timeout_seconds, trust_env=False) as client:
            resp = await client.get(
                f"{base_url}/bank/pay/bjtOrderStatus", params=params, headers=_headers()
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("[operate-recharge] query failed for %s: %s", pay_mer_tran_no, exc)
        return "pending"

    if body.get("code") != 200:
        logger.warning("[operate-recharge] query non-200 for %s: %s", pay_mer_tran_no, body.get("msg"))
        return "pending"
    data = body.get("data") or {}
    return data.get("status") or "pending"
