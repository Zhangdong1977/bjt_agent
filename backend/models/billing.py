"""Billing, wallet, order and consumption models."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User


class UserWallet(Base):
    """User wallet for AI check balance and points."""

    __tablename__ = "user_wallets"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    balance_wen: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    user: Mapped["User"] = relationship("User")


class WalletTransaction(Base):
    """Wallet ledger. balance_delta_wen and points_delta may be positive or negative."""

    __tablename__ = "wallet_transactions"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    balance_delta_wen: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    balance_after_wen: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    points_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    points_after: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reference_type: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class BillingOrder(Base):
    """Recharge order for AI check balance."""

    __tablename__ = "billing_orders"

    order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)

    order_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_payment_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    package_balance_wen: Mapped[int] = mapped_column(Integer, nullable=False)

    coupon_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    coupon_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coupon_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    points_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    points_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balance_after_wen: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 真实交行支付桥接：operate-two 返回的 payMerTranNo（mock 支付留空）
    external_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 真实交行支付二维码文本（displayCodeText），缓存以便重复渲染同一订单而不重复下单
    external_qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConsumptionRecord(Base):
    """AI check consumption record."""

    __tablename__ = "consumption_records"
    __table_args__ = (UniqueConstraint("task_id", name="uq_consumption_records_task_id"),)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    consumed_wen: Mapped[int] = mapped_column(Integer, nullable=False)
    earned_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    used_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cost_cny: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    balance_after_wen: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
