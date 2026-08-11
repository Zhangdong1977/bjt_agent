"""Schemas shared by operate-two sales management and bjt-agent runtime."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class SalesConfigPayload(BaseModel):
    sales_multiplier: float = Field(gt=0, le=1000)
    low_balance_threshold: float = Field(ge=0)
    version: int = Field(ge=1)
    # Optional per-feature overrides. Omitted/None means "use sales_multiplier".
    # Kept optional so an older operate-two that still pushes only the single
    # global multiplier continues to be accepted by this payload.
    # A value of 0 makes the feature free for all users (limited-time
    # promotion); the global sales_multiplier above stays strictly positive so
    # an unset feature still bills at the global rate by default.
    review_multiplier: float | None = Field(default=None, ge=0, le=1000)
    duplicate_multiplier: float | None = Field(default=None, ge=0, le=1000)
    blind_check_multiplier: float | None = Field(default=None, ge=0, le=1000)


class SalesPackagePayload(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    icon_url: str | None = Field(default=None, max_length=500)
    amount_cents: int = Field(gt=0)
    recharge_points: float = Field(ge=0)
    gift_points: float = Field(ge=0)
    validity_months: int = Field(default=12, ge=1, le=120)
    loyalty_deduction_limit: int | None = Field(default=None, ge=0)
    is_online: bool = True
    sort_order: int = 0
    caution: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_points(self):
        if self.recharge_points + self.gift_points <= 0:
            raise ValueError("套餐合计点数必须大于0")
        return self


class SalesSnapshotPayload(BaseModel):
    config: SalesConfigPayload
    packages: list[SalesPackagePayload]


class GrantRecipientPayload(BaseModel):
    external_user_id: int
    username: str = Field(min_length=1, max_length=100)
    nickname: str | None = Field(default=None, max_length=100)
    enterprise_name: str | None = Field(default=None, max_length=200)


class GrantCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    points_per_account: float = Field(gt=0)
    validity_value: int = Field(gt=0)
    validity_unit: str
    reason: str = Field(min_length=1, max_length=500)
    remark: str | None = None
    created_by: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)
    recipients: list[GrantRecipientPayload] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_validity(self):
        if self.validity_unit not in {"day", "month"}:
            raise ValueError("有效期单位仅支持day或month")
        return self


class GrantStopPayload(BaseModel):
    operator: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class GrantSummaryResponse(BaseModel):
    cumulative_points: float
    used_points: float
    unused_points: float
    expired_points: float
    voided_points: float


class GrantBatchResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    account_count: int
    points_per_account: float
    total_points: float
    total_value_yuan: float
    validity_value: int
    validity_unit: str
    reason: str
    remark: str | None
    termination_status: str
    generated_cost_yuan: float


class GrantDetailResponse(BaseModel):
    lot_id: str
    external_user_id: int | None
    username: str
    nickname: str | None
    initial_points: float
    used_points: float
    remaining_points: float
    status: str
    expires_at: datetime
    stopped_at: datetime | None
    stop_reason: str | None
    generated_cost_yuan: float
