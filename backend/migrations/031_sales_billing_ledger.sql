-- Migration 031: configurable sales packages and independently expiring
-- recharge/gift point lots. Compatible with PostgreSQL 10.
--
-- Run the historical reconstruction script after this migration. Until then
-- the split wallet columns remain zero and legacy balance_wen is untouched.

ALTER TABLE user_wallets
    ADD COLUMN IF NOT EXISTS recharge_balance_points NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE user_wallets
    ADD COLUMN IF NOT EXISTS gift_balance_points NUMERIC(16, 2) NOT NULL DEFAULT 0;

ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS recharge_points NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS gift_points NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS total_points NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS validity_months INTEGER NOT NULL DEFAULT 12;
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS unit_value_yuan NUMERIC(16, 8);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS recharge_balance_before NUMERIC(16, 2);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS gift_balance_before NUMERIC(16, 2);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS recharge_balance_after NUMERIC(16, 2);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS gift_balance_after NUMERIC(16, 2);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS coupon_benefit_type VARCHAR(20);
ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS coupon_gift_points NUMERIC(16, 2) NOT NULL DEFAULT 0;

ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS cost_points NUMERIC(18, 6);
ALTER TABLE consumption_records
    ALTER COLUMN cost_points TYPE NUMERIC(18, 6) USING cost_points::NUMERIC(18, 6);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS sales_multiplier NUMERIC(10, 4);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS sales_points NUMERIC(16, 2);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS gift_points_used NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS recharge_points_used NUMERIC(16, 2) NOT NULL DEFAULT 0;
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS recharge_balance_before NUMERIC(16, 2);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS gift_balance_before NUMERIC(16, 2);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS recharge_balance_after NUMERIC(16, 2);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS gift_balance_after NUMERIC(16, 2);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS weighted_unit_value_yuan NUMERIC(16, 8);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS folded_income_yuan NUMERIC(16, 6);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS profit_yuan NUMERIC(16, 6);
ALTER TABLE consumption_records
    ADD COLUMN IF NOT EXISTS profit_margin NUMERIC(12, 6);

ALTER TABLE review_tasks
    ADD COLUMN IF NOT EXISTS billing_multiplier NUMERIC(10, 4);

CREATE TABLE IF NOT EXISTS sales_configs (
    id VARCHAR(36) PRIMARY KEY,
    sales_multiplier NUMERIC(10, 4) NOT NULL DEFAULT 4,
    low_balance_threshold NUMERIC(16, 2) NOT NULL DEFAULT 0,
    config_version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_packages (
    id VARCHAR(36) PRIMARY KEY,
    code VARCHAR(40) NOT NULL,
    name VARCHAR(100) NOT NULL,
    icon_url VARCHAR(500),
    amount_cents INTEGER NOT NULL,
    recharge_points NUMERIC(16, 2) NOT NULL,
    gift_points NUMERIC(16, 2) NOT NULL DEFAULT 0,
    validity_months INTEGER NOT NULL DEFAULT 12,
    loyalty_deduction_limit INTEGER,
    is_online BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    caution VARCHAR(255),
    config_version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_sales_packages_code UNIQUE (code),
    CONSTRAINT ck_sales_packages_amount_positive CHECK (amount_cents > 0),
    CONSTRAINT ck_sales_packages_points_positive CHECK (recharge_points + gift_points > 0),
    CONSTRAINT ck_sales_packages_validity_positive CHECK (validity_months > 0)
);

CREATE INDEX IF NOT EXISTS ix_sales_packages_code ON sales_packages(code);
CREATE INDEX IF NOT EXISTS ix_sales_packages_is_online ON sales_packages(is_online);

CREATE TABLE IF NOT EXISTS grant_batches (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    points_per_account NUMERIC(16, 2) NOT NULL,
    validity_value INTEGER NOT NULL,
    validity_unit VARCHAR(10) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    remark TEXT,
    account_count INTEGER NOT NULL DEFAULT 0,
    total_points NUMERIC(18, 2) NOT NULL DEFAULT 0,
    created_by VARCHAR(100) NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_grant_batches_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT ck_grant_batches_points_positive CHECK (points_per_account > 0),
    CONSTRAINT ck_grant_batches_validity CHECK (
        validity_value > 0 AND validity_unit IN ('day', 'month')
    )
);

CREATE INDEX IF NOT EXISTS ix_grant_batches_name ON grant_batches(name);

CREATE TABLE IF NOT EXISTS credit_lots (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    external_user_id BIGINT,
    lot_type VARCHAR(20) NOT NULL,
    source_type VARCHAR(30) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(36) REFERENCES grant_batches(id) ON DELETE SET NULL,
    initial_points NUMERIC(16, 2) NOT NULL,
    remaining_points NUMERIC(16, 2) NOT NULL,
    unit_value_yuan NUMERIC(16, 8) NOT NULL DEFAULT 0,
    valid_from TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    stopped_at TIMESTAMPTZ,
    stopped_by VARCHAR(100),
    stop_reason VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_credit_lot_source_user_type UNIQUE (source_type, source_id, user_id, lot_type),
    CONSTRAINT ck_credit_lots_type CHECK (lot_type IN ('recharge', 'gift')),
    CONSTRAINT ck_credit_lots_status CHECK (status IN ('active', 'exhausted', 'expired', 'stopped')),
    CONSTRAINT ck_credit_lots_points CHECK (initial_points > 0 AND remaining_points >= 0)
);

CREATE INDEX IF NOT EXISTS ix_credit_lots_user_id ON credit_lots(user_id);
CREATE INDEX IF NOT EXISTS ix_credit_lots_external_user_id ON credit_lots(external_user_id);
CREATE INDEX IF NOT EXISTS ix_credit_lots_lot_type ON credit_lots(lot_type);
CREATE INDEX IF NOT EXISTS ix_credit_lots_source_type ON credit_lots(source_type);
CREATE INDEX IF NOT EXISTS ix_credit_lots_source_id ON credit_lots(source_id);
CREATE INDEX IF NOT EXISTS ix_credit_lots_batch_id ON credit_lots(batch_id);
CREATE INDEX IF NOT EXISTS ix_credit_lots_expires_at ON credit_lots(expires_at);
CREATE INDEX IF NOT EXISTS ix_credit_lots_status ON credit_lots(status);
CREATE INDEX IF NOT EXISTS ix_credit_lots_active_fifo
    ON credit_lots(user_id, lot_type, expires_at, created_at)
    WHERE status = 'active' AND remaining_points > 0;

CREATE TABLE IF NOT EXISTS point_ledger_entries (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type VARCHAR(30) NOT NULL,
    recharge_delta NUMERIC(16, 2) NOT NULL DEFAULT 0,
    gift_delta NUMERIC(16, 2) NOT NULL DEFAULT 0,
    loyalty_delta INTEGER NOT NULL DEFAULT 0,
    recharge_after NUMERIC(16, 2) NOT NULL,
    gift_after NUMERIC(16, 2) NOT NULL,
    loyalty_after INTEGER NOT NULL,
    lot_id VARCHAR(36) REFERENCES credit_lots(id) ON DELETE SET NULL,
    reference_type VARCHAR(30) NOT NULL,
    reference_id VARCHAR(64) NOT NULL,
    description VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_point_ledger_event_reference_lot
        UNIQUE (event_type, reference_type, reference_id, lot_id)
);

CREATE INDEX IF NOT EXISTS ix_point_ledger_entries_user_id ON point_ledger_entries(user_id);
CREATE INDEX IF NOT EXISTS ix_point_ledger_entries_event_type ON point_ledger_entries(event_type);
CREATE INDEX IF NOT EXISTS ix_point_ledger_entries_lot_id ON point_ledger_entries(lot_id);
CREATE INDEX IF NOT EXISTS ix_point_ledger_entries_reference_type ON point_ledger_entries(reference_type);
CREATE INDEX IF NOT EXISTS ix_point_ledger_entries_reference_id ON point_ledger_entries(reference_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_point_ledger_event_reference_lot_nullsafe
    ON point_ledger_entries(event_type, reference_type, reference_id, COALESCE(lot_id, ''));

CREATE TABLE IF NOT EXISTS consumption_allocations (
    id VARCHAR(36) PRIMARY KEY,
    consumption_id VARCHAR(36) NOT NULL REFERENCES consumption_records(id) ON DELETE CASCADE,
    lot_id VARCHAR(36) REFERENCES credit_lots(id) ON DELETE SET NULL,
    lot_type VARCHAR(20) NOT NULL,
    points NUMERIC(16, 2) NOT NULL,
    unit_value_yuan NUMERIC(16, 8) NOT NULL,
    folded_income_yuan NUMERIC(16, 6) NOT NULL,
    allocated_cost_yuan NUMERIC(16, 6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_consumption_allocation_lot UNIQUE (consumption_id, lot_id),
    CONSTRAINT ck_consumption_allocations_type CHECK (lot_type IN ('recharge', 'gift')),
    CONSTRAINT ck_consumption_allocations_points CHECK (points > 0)
);

CREATE INDEX IF NOT EXISTS ix_consumption_allocations_consumption_id
    ON consumption_allocations(consumption_id);
CREATE INDEX IF NOT EXISTS ix_consumption_allocations_lot_id
    ON consumption_allocations(lot_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consumption_allocation_lot_nullsafe
    ON consumption_allocations(consumption_id, COALESCE(lot_id, ''));

INSERT INTO sales_configs (
    id, sales_multiplier, low_balance_threshold, config_version, created_at, updated_at
) VALUES ('default', 4, 0, 1, NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales_packages (
    id, code, name, icon_url, amount_cents, recharge_points, gift_points,
    validity_months, loyalty_deduction_limit, is_online, sort_order, caution,
    config_version, created_at, updated_at
) VALUES
    ('sales-pkg-experience', 'experience', '体验套餐', 'plan-icon-trial', 3000, 300, 50, 12, NULL, TRUE, 10, '500页以上标书谨慎使用', 1, NOW(), NOW()),
    ('sales-pkg-basic', 'basic', '基础套餐', 'plan-icon-basic', 10000, 1000, 200, 12, NULL, TRUE, 20, NULL, 1, NOW(), NOW()),
    ('sales-pkg-premium', 'premium', '尊享套餐', 'plan-icon-premium', 30000, 3000, 1000, 12, NULL, TRUE, 30, NULL, 1, NOW(), NOW()),
    ('sales-pkg-luxury', 'luxury', '豪华套餐', 'plan-icon-luxury', 100000, 10000, 5000, 12, NULL, TRUE, 40, NULL, 1, NOW(), NOW())
ON CONFLICT (code) DO NOTHING;

COMMENT ON COLUMN user_wallets.recharge_balance_points IS '充值点数余额，可因结算超额而为负';
COMMENT ON COLUMN user_wallets.gift_balance_points IS '赠送点数余额，不允许为负';
COMMENT ON COLUMN review_tasks.billing_multiplier IS '任务发起时固化的销售倍率';
