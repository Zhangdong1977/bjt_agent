"""Sales configuration and auditable point-lot models.

The legacy billing tables keep their public fields for compatibility.  These
tables are the source of truth for the split recharge/gift wallet introduced
by the sales-management feature.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SalesConfig(Base):
    """Runtime copy of the operate-platform sales configuration."""

    __tablename__ = "sales_configs"

    sales_multiplier: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, default=4, server_default="4"
    )
    low_balance_threshold: Mapped[float] = mapped_column(
        Numeric(16, 2), nullable=False, default=0, server_default="0"
    )
    config_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default="1"
    )


class SalesPackage(Base):
    """Versioned runtime package copied from operate-two."""

    __tablename__ = "sales_packages"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    recharge_points: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    gift_points: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0, server_default="0")
    validity_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    loyalty_deduction_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    caution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")


class GrantBatch(Base):
    """One operator-created bulk gift campaign."""

    __tablename__ = "grant_batches"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    points_per_account: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    validity_value: Mapped[int] = mapped_column(Integer, nullable=False)
    validity_unit: Mapped[str] = mapped_column(String(10), nullable=False)  # day/month
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_points: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)


class CreditLot(Base):
    """Independently expiring recharge or gift point lot."""

    __tablename__ = "credit_lots"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "user_id", "lot_type", name="uq_credit_lot_source_user_type"),
    )

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    external_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    lot_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # recharge/gift
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("grant_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    initial_points: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    remaining_points: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    unit_value_yuan: Mapped[float] = mapped_column(Numeric(16, 8), nullable=False, default=0, server_default="0")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active", index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PointLedgerEntry(Base):
    """Immutable wallet event ledger."""

    __tablename__ = "point_ledger_entries"
    __table_args__ = (
        UniqueConstraint("event_type", "reference_type", "reference_id", "lot_id", name="uq_point_ledger_event_reference_lot"),
    )

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    recharge_delta: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0, server_default="0")
    gift_delta: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0, server_default="0")
    loyalty_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    recharge_after: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    gift_after: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    loyalty_after: Mapped[int] = mapped_column(Integer, nullable=False)
    lot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("credit_lots.id", ondelete="SET NULL"), nullable=True, index=True)
    reference_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ConsumptionAllocation(Base):
    """Point-lot allocation for one settled consumption."""

    __tablename__ = "consumption_allocations"
    __table_args__ = (
        UniqueConstraint("consumption_id", "lot_id", name="uq_consumption_allocation_lot"),
    )

    consumption_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("consumption_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("credit_lots.id", ondelete="SET NULL"), nullable=True, index=True)
    lot_type: Mapped[str] = mapped_column(String(20), nullable=False)
    points: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    unit_value_yuan: Mapped[float] = mapped_column(Numeric(16, 8), nullable=False)
    folded_income_yuan: Mapped[float] = mapped_column(Numeric(16, 6), nullable=False)
    allocated_cost_yuan: Mapped[float] = mapped_column(Numeric(16, 6), nullable=False, default=0, server_default="0")
