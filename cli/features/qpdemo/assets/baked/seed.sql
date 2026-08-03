-- RDST QueryPilot demo canonical bootstrap: Orders schema plus deterministic seed.
--
-- Target scale:
--   * 100,000 customers
--   * 5,000 products
--   * 1,500,000 orders
--   * about 4,000,000 order_items
--
-- Distribution:
--   * deterministic random stream via setseed(), with a fixed timestamp anchor
--   * top 1% of customers (ids 1-1000) receive about 42% of orders
--   * hot products (ids 1-250) receive about 55% of line items
--   * order dates span about 18 months, biased toward recent activity
--
-- Index strategy:
--   * Keep only primary-key and unique indexes. Cheap point lookups in the demo
--     hit customers/products/orders/order_items primary keys, or customers.email,
--     and should stay sub-millisecond when warm.
--   * Deliberately do not add secondary indexes such as orders(customer_id),
--     orders(placed_at), order_items(order_id), or order_items(product_id).
--     Those would make the aggregate-heavy dashboard queries too cheap; the demo
--     needs those uncached scans/joins/groups to land around 100-500ms warm so
--     QueryPilot's cache choice is visible.
--
-- Keep features/qpdemo/dataset/schema.sql aligned with this table/index shape.

SET client_min_messages TO warning;
SET synchronous_commit TO off;
SET maintenance_work_mem TO '512MB';

SELECT setseed(0.424242);

BEGIN;

DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    id         bigint PRIMARY KEY,
    email      text UNIQUE NOT NULL,
    full_name  text NOT NULL,
    country    text NOT NULL,
    segment    text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE products (
    id          bigint PRIMARY KEY,
    name        text NOT NULL,
    category    text NOT NULL,
    price_cents integer NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL
);

CREATE TABLE orders (
    id          bigint PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers(id),
    placed_at   timestamptz NOT NULL,
    status      text NOT NULL,
    channel     text NOT NULL,
    total_cents integer NOT NULL
);

CREATE TABLE order_items (
    id         bigint PRIMARY KEY,
    order_id   bigint NOT NULL REFERENCES orders(id),
    product_id bigint NOT NULL REFERENCES products(id),
    qty        integer NOT NULL,
    unit_cents integer NOT NULL
);

INSERT INTO customers (id, email, full_name, country, segment, created_at)
SELECT
    g::bigint,
    'user' || g || '@example.com',
    'Customer ' || g,
    (ARRAY[
        'US','US','US','US','CA','GB','DE','FR','AU','NL',
        'JP','BR','IN','MX','SE','ES'
    ])[1 + floor(random() * 16)::int],
    CASE
        WHEN g <= 1000 AND random() < 0.28 THEN 'enterprise'
        WHEN random() < 0.62 THEN 'consumer'
        WHEN random() < 0.78 THEN 'smb'
        ELSE 'enterprise'
    END,
    timestamp with time zone '2026-07-09 12:00:00+00'
        - ((random() ^ 1.35) * interval '620 days')
FROM generate_series(1, 100000) AS g;

INSERT INTO products (id, name, category, price_cents, active, created_at)
SELECT
    g::bigint,
    'Product ' || g,
    (ARRAY[
        'electronics','books','home','toys','garden','beauty',
        'sports','grocery','apparel','auto','office','pet'
    ])[1 + ((g - 1) % 12)],
    (500 + ((g * 7919) % 90000))::int,
    (g % 23 <> 0),
    timestamp with time zone '2026-07-09 12:00:00+00'
        - ((random() ^ 1.15) * interval '760 days')
FROM generate_series(1, 5000) AS g;

INSERT INTO orders (id, customer_id, placed_at, status, channel, total_cents)
SELECT
    g::bigint,
    CASE
        WHEN random() < 0.42
            THEN (1 + floor((random() ^ 1.55) * 1000))::bigint
        ELSE (1001 + floor(random() * 99000))::bigint
    END,
    timestamp with time zone '2026-07-09 12:00:00+00'
        - ((random() ^ 2.15) * interval '548 days')
        - (floor(random() * 86400.0)::int * interval '1 second'),
    (ARRAY[
        'placed','paid','paid','shipped','shipped',
        'delivered','delivered','delivered','cancelled','refunded'
    ])[1 + floor(random() * 10)::int],
    (ARRAY['web','web','web','mobile','mobile','partner'])[1 + floor(random() * 6)::int],
    (1000 + floor(random() * 180000))::int
FROM generate_series(1, 1500000) AS g;

WITH item_draws AS (
    SELECT id AS order_id, random() AS item_draw
    FROM orders
),
item_counts AS (
    SELECT
        order_id,
        CASE
            WHEN item_draw < 0.10 THEN 1
            WHEN item_draw < 0.45 THEN 2
            WHEN item_draw < 0.80 THEN 3
            ELSE 4
        END AS item_count
    FROM item_draws
),
line_products AS (
    SELECT
        ic.order_id,
        gs.line_no,
        CASE
            WHEN random() < 0.55
                THEN (1 + floor((random() ^ 1.30) * 250))::bigint
            ELSE (251 + floor(random() * 4750))::bigint
        END AS product_id,
        (1 + floor(random() * 4))::int AS qty
    FROM item_counts ic
    CROSS JOIN LATERAL generate_series(1, ic.item_count) AS gs(line_no)
)
INSERT INTO order_items (id, order_id, product_id, qty, unit_cents)
SELECT
    row_number() OVER (ORDER BY order_id, line_no)::bigint,
    order_id,
    product_id,
    qty,
    (500 + ((product_id * 7919) % 90000))::int
FROM line_products;

CREATE TEMP TABLE order_totals ON COMMIT DROP AS
SELECT order_id, sum(qty * unit_cents)::integer AS total_cents
FROM order_items
GROUP BY order_id;

UPDATE orders o
SET total_cents = ot.total_cents
FROM order_totals ot
WHERE ot.order_id = o.id;

ANALYZE customers;
ANALYZE products;
ANALYZE orders;
ANALYZE order_items;

COMMIT;
