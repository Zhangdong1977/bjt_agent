"""Billing API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class WalletResponse(BaseModel):
    balance_wen: int
    points: int


class PackageResponse(BaseModel):
    code: str
    name: str
    amount_cents: int
    balance_wen: int
    caution: str | None = None
    payment_mode: str = "mock"  # "real"（真实交行聚合支付）| "mock"（模拟支付）


class CouponResponse(BaseModel):
    id: int
    code: str | None = None
    amount_cents: int
    amount_yuan: float
    valid_until: datetime | None = None
    status: str
    raw_status: int | None = None


class OrderPreviewRequest(BaseModel):
    package_code: str
    coupon_id: int | None = None
    use_points: int = Field(default=0, ge=0)


class OrderCreateRequest(OrderPreviewRequest):
    accepted_agreement: bool


class OrderPreviewResponse(BaseModel):
    package_code: str
    product_name: str
    order_amount_cents: int
    coupon_amount_cents: int
    points_used: int
    points_amount_cents: int
    actual_payment_cents: int
    package_balance_wen: int
    current_balance_wen: int
    current_points: int


class OrderResponse(BaseModel):
    id: str
    order_no: str
    product_name: str
    created_at: datetime
    status: str
    order_amount_cents: int
    actual_payment_cents: int
    coupon_code: str | None = None
    coupon_amount_cents: int
    points_used: int
    expires_at: datetime
    paid_at: datetime | None = None
    balance_after_wen: int | None = None
    current_balance_wen: int | None = None


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]


class ConsumptionResponse(BaseModel):
    id: str
    consumed_at: datetime
    project_name: str
    consumed_wen: int
    earned_points: int
    used_by: str
    cost_cny: float | None = None


class ConsumptionListResponse(BaseModel):
    consumptions: list[ConsumptionResponse]


class PaymentQrResponse(BaseModel):
    order_id: str
    order_no: str
    actual_payment_cents: int
    payment_mode: str = "mock"  # "real" | "mock"
    qr_payload: str  # real: 交行二维码文本；mock: mockpay://...
    expires_at: datetime


class OrderStatusResponse(BaseModel):
    order_id: str
    order_no: str
    status: str
    paid_at: datetime | None = None
    balance_after_wen: int | None = None
