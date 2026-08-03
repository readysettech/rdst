-- Many-table workload for testing rdst schema annotate batching/parallelism.
-- All tables are namespaced rdst_annot_test_* so cleanup is trivial:
--   DROP TABLE rdst_annot_test_NN CASCADE;
-- Or batch:
--   DO $$ DECLARE r record; BEGIN FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'rdst_annot_test_%' LOOP EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.tablename); END LOOP; END $$;

-- 25 tables, mix of business domains so the LLM has real semantic content to annotate.

CREATE TABLE IF NOT EXISTS rdst_annot_test_customers (
    id BIGSERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    full_name TEXT,
    phone TEXT,
    country_code CHAR(2),
    customer_tier TEXT CHECK (customer_tier IN ('free','pro','enterprise')),
    signup_at TIMESTAMPTZ DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_orders (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    order_status TEXT CHECK (order_status IN ('pending','paid','shipped','delivered','cancelled','refunded')),
    placed_at TIMESTAMPTZ DEFAULT now(),
    fulfilled_at TIMESTAMPTZ,
    subtotal_cents BIGINT,
    tax_cents BIGINT,
    total_cents BIGINT,
    currency CHAR(3) DEFAULT 'USD'
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    sku TEXT,
    quantity INT NOT NULL,
    unit_price_cents BIGINT,
    discount_cents BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    list_price_cents BIGINT,
    weight_grams INT,
    is_digital BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_inventory (
    sku TEXT PRIMARY KEY,
    warehouse_id INT,
    on_hand INT DEFAULT 0,
    reserved INT DEFAULT 0,
    reorder_threshold INT,
    last_counted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_warehouses (
    id SERIAL PRIMARY KEY,
    name TEXT,
    region TEXT,
    capacity_cubic_meters NUMERIC(10,2),
    opened_at DATE
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT,
    method TEXT CHECK (method IN ('card','ach','paypal','wire','crypto')),
    status TEXT CHECK (status IN ('initiated','authorized','captured','failed','refunded')),
    amount_cents BIGINT,
    processor_ref TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_refunds (
    id BIGSERIAL PRIMARY KEY,
    payment_id BIGINT,
    amount_cents BIGINT,
    reason_code TEXT,
    issued_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    plan_code TEXT,
    status TEXT CHECK (status IN ('trialing','active','past_due','cancelled','expired')),
    started_at TIMESTAMPTZ,
    renews_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    monthly_price_cents BIGINT
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_invoices (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    period_start DATE,
    period_end DATE,
    issued_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    amount_cents BIGINT,
    pdf_url TEXT
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE,
    email TEXT,
    role TEXT CHECK (role IN ('admin','manager','agent','viewer')),
    department TEXT,
    hired_at DATE,
    terminated_at DATE
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT,
    ip_address INET,
    user_agent TEXT,
    started_at TIMESTAMPTZ DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT,
    action TEXT,
    entity_type TEXT,
    entity_id TEXT,
    before_json JSONB,
    after_json JSONB,
    occurred_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_tickets (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    assignee_user_id BIGINT,
    subject TEXT,
    body TEXT,
    priority TEXT CHECK (priority IN ('low','normal','high','urgent')),
    status TEXT CHECK (status IN ('open','in_progress','waiting','resolved','closed')),
    opened_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_messages (
    id BIGSERIAL PRIMARY KEY,
    ticket_id BIGINT,
    author_user_id BIGINT,
    body TEXT,
    sent_at TIMESTAMPTZ DEFAULT now(),
    is_internal BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_campaigns (
    id BIGSERIAL PRIMARY KEY,
    name TEXT,
    channel TEXT CHECK (channel IN ('email','sms','push','in_app')),
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    target_segment TEXT,
    budget_cents BIGINT
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_email_events (
    id BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT,
    customer_id BIGINT,
    event_type TEXT CHECK (event_type IN ('sent','delivered','opened','clicked','bounced','unsubscribed','complained')),
    occurred_at TIMESTAMPTZ DEFAULT now(),
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_page_views (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID,
    user_id BIGINT,
    path TEXT,
    referrer TEXT,
    duration_ms INT,
    occurred_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_feature_flags (
    key TEXT PRIMARY KEY,
    description TEXT,
    enabled_globally BOOLEAN DEFAULT false,
    rollout_percent INT CHECK (rollout_percent BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_feature_flag_overrides (
    id BIGSERIAL PRIMARY KEY,
    flag_key TEXT,
    customer_id BIGINT,
    enabled BOOLEAN,
    reason TEXT,
    set_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_api_keys (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    key_hash TEXT,
    label TEXT,
    scopes TEXT[],
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_webhooks (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT,
    url TEXT,
    secret TEXT,
    event_types TEXT[],
    is_active BOOLEAN DEFAULT true,
    last_delivered_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_webhook_deliveries (
    id BIGSERIAL PRIMARY KEY,
    webhook_id BIGINT,
    event_type TEXT,
    payload JSONB,
    status_code INT,
    response_body TEXT,
    delivered_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_partner_accounts (
    id BIGSERIAL PRIMARY KEY,
    name TEXT,
    contact_email TEXT,
    tier TEXT CHECK (tier IN ('bronze','silver','gold','platinum')),
    commission_basis_points INT,
    onboarded_at DATE,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS rdst_annot_test_referrals (
    id BIGSERIAL PRIMARY KEY,
    partner_id BIGINT,
    referred_customer_id BIGINT,
    converted_at TIMESTAMPTZ,
    commission_cents BIGINT,
    payout_status TEXT CHECK (payout_status IN ('pending','paid','reversed'))
);

-- Insert a few rows in each so the introspection / sample-row reads have content.
INSERT INTO rdst_annot_test_customers (email, full_name, country_code, customer_tier)
SELECT 'user' || i || '@example.com', 'User ' || i, (ARRAY['US','GB','DE','FR','JP'])[1 + i % 5], (ARRAY['free','pro','enterprise'])[1 + i % 3]
FROM generate_series(1, 30) i
ON CONFLICT (email) DO NOTHING;

INSERT INTO rdst_annot_test_products (sku, name, category, list_price_cents)
SELECT 'SKU-' || lpad(i::text, 4, '0'), 'Product ' || i, (ARRAY['electronics','apparel','home','grocery','books'])[1 + i % 5], (i * 199)
FROM generate_series(1, 50) i
ON CONFLICT (sku) DO NOTHING;

INSERT INTO rdst_annot_test_warehouses (name, region) VALUES
    ('West', 'us-west-2'), ('East', 'us-east-1'), ('Central', 'us-central-1'), ('EU', 'eu-west-1'), ('APAC', 'ap-northeast-1')
ON CONFLICT DO NOTHING;

INSERT INTO rdst_annot_test_feature_flags (key, description, enabled_globally, rollout_percent) VALUES
    ('checkout_v2', 'New checkout flow', false, 10),
    ('search_rerank', 'ML reranking on search', true, 100),
    ('mobile_push', 'Mobile push notifications', false, 0)
ON CONFLICT DO NOTHING;
