-- RDST QueryPilot demo canonical Orders schema.
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
-- The Docker bootstrap in ../assets/seed.sql contains this same table/index
-- shape plus deterministic generated data. Keep this schema-only file aligned
-- with that bootstrap when changing table definitions.

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

COMMIT;
