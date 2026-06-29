-- Migration 016: user profile + billing/wallet tables for AI check recharge.
-- New databases are covered by init_db() create_all. Existing databases should
-- run this file manually via psql.

ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS company VARCHAR(200);
ALTER TABLE users ADD COLUMN IF NOT EXISTS bidding_industries TEXT;

CREATE TABLE IF NOT EXISTS user_wallets (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    balance_wen INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_user_wallets_user_id UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS ix_user_wallets_user_id ON user_wallets(user_id);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_type VARCHAR(30) NOT NULL,
    balance_delta_wen INTEGER NOT NULL DEFAULT 0,
    balance_after_wen INTEGER NOT NULL DEFAULT 0,
    points_delta INTEGER NOT NULL DEFAULT 0,
    points_after INTEGER NOT NULL DEFAULT 0,
    reference_type VARCHAR(30),
    reference_id VARCHAR(64),
    description VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_id ON wallet_transactions(user_id);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_transaction_type ON wallet_transactions(transaction_type);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_reference_type ON wallet_transactions(reference_type);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_reference_id ON wallet_transactions(reference_id);

CREATE TABLE IF NOT EXISTS billing_orders (
    id VARCHAR(36) PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL UNIQUE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_code VARCHAR(40) NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    order_amount_cents INTEGER NOT NULL,
    actual_payment_cents INTEGER NOT NULL,
    package_balance_wen INTEGER NOT NULL,
    coupon_id INTEGER,
    coupon_code VARCHAR(64),
    coupon_amount_cents INTEGER NOT NULL DEFAULT 0,
    points_used INTEGER NOT NULL DEFAULT 0,
    points_amount_cents INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    paid_at TIMESTAMPTZ,
    balance_after_wen INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_billing_orders_order_no ON billing_orders(order_no);
CREATE INDEX IF NOT EXISTS ix_billing_orders_user_id ON billing_orders(user_id);
CREATE INDEX IF NOT EXISTS ix_billing_orders_product_code ON billing_orders(product_code);
CREATE INDEX IF NOT EXISTS ix_billing_orders_product_name ON billing_orders(product_name);
CREATE INDEX IF NOT EXISTS ix_billing_orders_status ON billing_orders(status);
CREATE INDEX IF NOT EXISTS ix_billing_orders_coupon_id ON billing_orders(coupon_id);
CREATE INDEX IF NOT EXISTS ix_billing_orders_expires_at ON billing_orders(expires_at);

CREATE TABLE IF NOT EXISTS consumption_records (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id VARCHAR(36) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    project_name VARCHAR(255) NOT NULL,
    consumed_wen INTEGER NOT NULL,
    earned_points INTEGER NOT NULL DEFAULT 0,
    used_by VARCHAR(100) NOT NULL,
    cost_cny NUMERIC(12,6),
    balance_after_wen INTEGER NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_consumption_records_task_id UNIQUE (task_id)
);

CREATE INDEX IF NOT EXISTS ix_consumption_records_user_id ON consumption_records(user_id);
CREATE INDEX IF NOT EXISTS ix_consumption_records_task_id ON consumption_records(task_id);
CREATE INDEX IF NOT EXISTS ix_consumption_records_project_id ON consumption_records(project_id);
CREATE INDEX IF NOT EXISTS ix_consumption_records_project_name ON consumption_records(project_name);
CREATE INDEX IF NOT EXISTS ix_consumption_records_used_by ON consumption_records(used_by);
