"""Billing API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class WalletResponse(BaseModel):
    balance_wen: int
    points: int
    recharge_balance_points: float = 0
    gift_balance_points: float = 0
    total_balance_points: float = 0
    low_balance_threshold: float = 0
    low_balance: bool = False


class PackageResponse(BaseModel):
    code: str
    name: str
    amount_cents: int
    balance_wen: int
    caution: str | None = None
    icon_url: str | None = None
    recharge_points: float = 0
    gift_points: float = 0
    total_points: float = 0
    validity_months: int = 12
    loyalty_deduction_limit: int | None = None


class CouponResponse(BaseModel):
    id: int
    code: str | None = None
    amount_cents: int
    amount_yuan: float
    valid_until: datetime | None = None
    status: str
    raw_status: int | None = None
    product_type: str = "plugin"
    benefit_type: str = "cash"
    threshold_amount_cents: int = 0
    gift_points: float = 0


class CouponRedeemRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)


class CouponRedeemResponse(BaseModel):
    coupon: CouponResponse | None = None
    coupons: list[CouponResponse]


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
    recharge_points: float = 0
    gift_points: float = 0
    total_points: float = 0
    validity_months: int = 12
    loyalty_deduction_limit: int | None = None
    current_recharge_points: float = 0
    current_gift_points: float = 0
    coupon_benefit_type: str | None = None
    coupon_gift_points: float = 0


class OrderResponse(BaseModel):
    id: str
    # 赠送行无订单号（运营赠送批次不产生订单）
    order_no: str | None = None
    # 来源：充值（recharge，来自 BillingOrder）/ 赠送（gift，来自运营赠送批次 CreditLot source_type=grant_batch）
    source: str = "recharge"
    product_name: str
    created_at: datetime
    status: str
    order_amount_cents: int
    actual_payment_cents: int
    coupon_code: str | None = None
    coupon_amount_cents: int
    points_used: int
    # 积分抵扣金额（分）
    points_amount_cents: int = 0
    expires_at: datetime
    paid_at: datetime | None = None
    balance_after_wen: int | None = None
    current_balance_wen: int | None = None
    # 归属信息（仅内部用户看全站时回填；外部用户视角为 None）
    username: str | None = None
    enterprise_name: str | None = None
    recharge_points: float = 0
    gift_points: float = 0
    total_points: float = 0
    recharge_balance_after: float | None = None
    gift_balance_after: float | None = None
    unit_value_yuan: float | None = None
    validity_months: int = 12
    coupon_benefit_type: str | None = None
    coupon_gift_points: float = 0
    consumed_points: float = 0
    remaining_points: float = 0
    points_expires_at: datetime | None = None
    points_status: str = "not_credited"


class OrderListResponse(BaseModel):
    orders: list[OrderResponse]


class ConsumptionResponse(BaseModel):
    id: str
    consumed_at: datetime
    project_name: str
    task_type: str = "review"
    task_status: str | None = None
    consumed_wen: int
    earned_points: int
    used_by: str
    cost_cny: float | None = None
    # 归属信息（仅内部用户看全站时回填；外部用户视角为 None）
    username: str | None = None
    enterprise_name: str | None = None
    cost_points: float | None = None
    sales_multiplier: float | None = None
    sales_points: float | None = None
    gift_points_used: float = 0
    recharge_points_used: float = 0
    # 消费前/后的充值/赠送点数余额（结算时已写入 consumption_records）
    recharge_balance_before: float | None = None
    gift_balance_before: float | None = None
    recharge_balance_after: float | None = None
    gift_balance_after: float | None = None
    # 本次扣点消耗的充值订单编号（经 consumption_allocations→credit_lots→billing_orders 聚合，多张以 ", " 连接）
    settlement_order_nos: str | None = None
    weighted_unit_value_yuan: float | None = None
    folded_income_yuan: float | None = None
    profit_yuan: float | None = None
    profit_margin: float | None = None


class ConsumptionListResponse(BaseModel):
    consumptions: list[ConsumptionResponse]


class ConsumptionAllocationResponse(BaseModel):
    id: str
    lot_id: str | None = None
    lot_type: str
    source_type: str | None = None
    source_id: str | None = None
    points: float
    unit_value_yuan: float
    folded_income_yuan: float
    expires_at: datetime | None = None


class ConsumptionAllocationListResponse(BaseModel):
    allocations: list[ConsumptionAllocationResponse]


class PaymentQrResponse(BaseModel):
    order_id: str
    order_no: str
    actual_payment_cents: int
    qr_payload: str  # 交行二维码文本
    expires_at: datetime


class OrderStatusResponse(BaseModel):
    order_id: str
    order_no: str
    status: str
    paid_at: datetime | None = None
    balance_after_wen: int | None = None
