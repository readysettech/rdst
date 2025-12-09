-- ============================================
-- q01.sql - TPC-H Query q01
-- ============================================
-- TPC-H Query 1: Pricing Summary Report
-- Tests: aggregation, filtering on date range
SELECT
    l_returnflag,
    l_linestatus,
    sum(l_quantity) as sum_qty,
    sum(l_extendedprice) as sum_base_price,
    sum(l_extendedprice * (1 - l_discount)) as sum_disc_price,
    sum(l_extendedprice * (1 - l_discount) * (1 + l_tax)) as sum_charge,
    avg(l_quantity) as avg_qty,
    avg(l_extendedprice) as avg_price,
    avg(l_discount) as avg_disc,
    count(*) as count_order
FROM
    lineitem
WHERE
    l_shipdate <= date '1998-12-01' - interval '90 day'
GROUP BY
    l_returnflag,
    l_linestatus
ORDER BY
    l_returnflag,
    l_linestatus;


-- ============================================
-- q02.sql - TPC-H Query q02
-- ============================================
-- TPC-H Query 2: Minimum Cost Supplier
-- Tests: correlated subquery, multi-table join
SELECT
    s_acctbal,
    s_name,
    n_name,
    p_partkey,
    p_mfgr,
    s_address,
    s_phone,
    s_comment
FROM
    part,
    supplier,
    partsupp,
    nation,
    region
WHERE
    p_partkey = ps_partkey
    AND s_suppkey = ps_suppkey
    AND p_size = 15
    AND p_type LIKE '%BRASS'
    AND s_nationkey = n_nationkey
    AND n_regionkey = r_regionkey
    AND r_name = 'EUROPE'
    AND ps_supplycost = (
        SELECT
            min(ps_supplycost)
        FROM
            partsupp,
            supplier,
            nation,
            region
        WHERE
            p_partkey = ps_partkey
            AND s_suppkey = ps_suppkey
            AND s_nationkey = n_nationkey
            AND n_regionkey = r_regionkey
            AND r_name = 'EUROPE'
    )
ORDER BY
    s_acctbal DESC,
    n_name,
    s_name,
    p_partkey
LIMIT 100;


-- ============================================
-- q03.sql - TPC-H Query q03
-- ============================================
-- TPC-H Query 3: Shipping Priority
-- Tests: 3-table join, date filtering, aggregation
SELECT
    l_orderkey,
    sum(l_extendedprice * (1 - l_discount)) as revenue,
    o_orderdate,
    o_shippriority
FROM
    customer,
    orders,
    lineitem
WHERE
    c_mktsegment = 'BUILDING'
    AND c_custkey = o_custkey
    AND l_orderkey = o_orderkey
    AND o_orderdate < date '1995-03-15'
    AND l_shipdate > date '1995-03-15'
GROUP BY
    l_orderkey,
    o_orderdate,
    o_shippriority
ORDER BY
    revenue DESC,
    o_orderdate
LIMIT 10;


-- ============================================
-- q04.sql - TPC-H Query q04
-- ============================================
-- TPC-H Query 4: Order Priority Checking
-- Tests: EXISTS subquery, date range filtering
SELECT
    o_orderpriority,
    count(*) as order_count
FROM
    orders
WHERE
    o_orderdate >= date '1993-07-01'
    AND o_orderdate < date '1993-07-01' + interval '3 month'
    AND EXISTS (
        SELECT *
        FROM lineitem
        WHERE l_orderkey = o_orderkey
        AND l_commitdate < l_receiptdate
    )
GROUP BY
    o_orderpriority
ORDER BY
    o_orderpriority;


-- ============================================
-- q05.sql - TPC-H Query q05
-- ============================================
-- TPC-H Query 5: Local Supplier Volume
-- Tests: 6-table join, date range, aggregation
SELECT
    n_name,
    sum(l_extendedprice * (1 - l_discount)) as revenue
FROM
    customer,
    orders,
    lineitem,
    supplier,
    nation,
    region
WHERE
    c_custkey = o_custkey
    AND l_orderkey = o_orderkey
    AND l_suppkey = s_suppkey
    AND c_nationkey = s_nationkey
    AND s_nationkey = n_nationkey
    AND n_regionkey = r_regionkey
    AND r_name = 'ASIA'
    AND o_orderdate >= date '1994-01-01'
    AND o_orderdate < date '1994-01-01' + interval '1 year'
GROUP BY
    n_name
ORDER BY
    revenue DESC;


-- ============================================
-- q06.sql - TPC-H Query q06
-- ============================================
-- TPC-H Query 6: Forecasting Revenue Change
-- Tests: simple scan with range predicates
SELECT
    sum(l_extendedprice * l_discount) as revenue
FROM
    lineitem
WHERE
    l_shipdate >= date '1994-01-01'
    AND l_shipdate < date '1994-01-01' + interval '1 year'
    AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01
    AND l_quantity < 24;


-- ============================================
-- q07.sql - TPC-H Query q07
-- ============================================
-- TPC-H Query 7: Volume Shipping
-- Tests: multi-table join with OR conditions
SELECT
    supp_nation,
    cust_nation,
    l_year,
    sum(volume) as revenue
FROM
    (
        SELECT
            n1.n_name as supp_nation,
            n2.n_name as cust_nation,
            extract(year from l_shipdate) as l_year,
            l_extendedprice * (1 - l_discount) as volume
        FROM
            supplier,
            lineitem,
            orders,
            customer,
            nation n1,
            nation n2
        WHERE
            s_suppkey = l_suppkey
            AND o_orderkey = l_orderkey
            AND c_custkey = o_custkey
            AND s_nationkey = n1.n_nationkey
            AND c_nationkey = n2.n_nationkey
            AND (
                (n1.n_name = 'FRANCE' AND n2.n_name = 'GERMANY')
                OR (n1.n_name = 'GERMANY' AND n2.n_name = 'FRANCE')
            )
            AND l_shipdate BETWEEN date '1995-01-01' AND date '1996-12-31'
    ) as shipping
GROUP BY
    supp_nation,
    cust_nation,
    l_year
ORDER BY
    supp_nation,
    cust_nation,
    l_year;


-- ============================================
-- q08.sql - TPC-H Query q08
-- ============================================
-- TPC-H Query 8: National Market Share
-- Tests: 8-table join, CASE expression
SELECT
    o_year,
    sum(CASE WHEN nation = 'BRAZIL' THEN volume ELSE 0 END) / sum(volume) as mkt_share
FROM
    (
        SELECT
            extract(year from o_orderdate) as o_year,
            l_extendedprice * (1 - l_discount) as volume,
            n2.n_name as nation
        FROM
            part,
            supplier,
            lineitem,
            orders,
            customer,
            nation n1,
            nation n2,
            region
        WHERE
            p_partkey = l_partkey
            AND s_suppkey = l_suppkey
            AND l_orderkey = o_orderkey
            AND o_custkey = c_custkey
            AND c_nationkey = n1.n_nationkey
            AND n1.n_regionkey = r_regionkey
            AND r_name = 'AMERICA'
            AND s_nationkey = n2.n_nationkey
            AND o_orderdate BETWEEN date '1995-01-01' AND date '1996-12-31'
            AND p_type = 'ECONOMY ANODIZED STEEL'
    ) as all_nations
GROUP BY
    o_year
ORDER BY
    o_year;


-- ============================================
-- q09.sql - TPC-H Query q09
-- ============================================
-- TPC-H Query 9: Product Type Profit Measure
-- Tests: 6-table join, LIKE pattern, aggregation
SELECT
    nation,
    o_year,
    sum(amount) as sum_profit
FROM
    (
        SELECT
            n_name as nation,
            extract(year from o_orderdate) as o_year,
            l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity as amount
        FROM
            part,
            supplier,
            lineitem,
            partsupp,
            orders,
            nation
        WHERE
            s_suppkey = l_suppkey
            AND ps_suppkey = l_suppkey
            AND ps_partkey = l_partkey
            AND p_partkey = l_partkey
            AND o_orderkey = l_orderkey
            AND s_nationkey = n_nationkey
            AND p_name LIKE '%green%'
    ) as profit
GROUP BY
    nation,
    o_year
ORDER BY
    nation,
    o_year DESC;


-- ============================================
-- q10.sql - TPC-H Query q10
-- ============================================
-- TPC-H Query 10: Returned Item Reporting
-- Tests: 4-table join, date range, aggregation
SELECT
    c_custkey,
    c_name,
    sum(l_extendedprice * (1 - l_discount)) as revenue,
    c_acctbal,
    n_name,
    c_address,
    c_phone,
    c_comment
FROM
    customer,
    orders,
    lineitem,
    nation
WHERE
    c_custkey = o_custkey
    AND l_orderkey = o_orderkey
    AND o_orderdate >= date '1993-10-01'
    AND o_orderdate < date '1993-10-01' + interval '3 month'
    AND l_returnflag = 'R'
    AND c_nationkey = n_nationkey
GROUP BY
    c_custkey,
    c_name,
    c_acctbal,
    c_phone,
    n_name,
    c_address,
    c_comment
ORDER BY
    revenue DESC
LIMIT 20;


-- ============================================
-- q11.sql - TPC-H Query q11
-- ============================================
-- TPC-H Query 11: Important Stock Identification
-- Tests: correlated subquery with aggregation
SELECT
    ps_partkey,
    sum(ps_supplycost * ps_availqty) as value
FROM
    partsupp,
    supplier,
    nation
WHERE
    ps_suppkey = s_suppkey
    AND s_nationkey = n_nationkey
    AND n_name = 'GERMANY'
GROUP BY
    ps_partkey
HAVING
    sum(ps_supplycost * ps_availqty) > (
        SELECT
            sum(ps_supplycost * ps_availqty) * 0.0001
        FROM
            partsupp,
            supplier,
            nation
        WHERE
            ps_suppkey = s_suppkey
            AND s_nationkey = n_nationkey
            AND n_name = 'GERMANY'
    )
ORDER BY
    value DESC;


-- ============================================
-- q12.sql - TPC-H Query q12
-- ============================================
-- TPC-H Query 12: Shipping Modes and Order Priority
-- Tests: 2-table join, CASE aggregation, IN list
SELECT
    l_shipmode,
    sum(CASE
        WHEN o_orderpriority = '1-URGENT' OR o_orderpriority = '2-HIGH'
        THEN 1 ELSE 0
    END) as high_line_count,
    sum(CASE
        WHEN o_orderpriority <> '1-URGENT' AND o_orderpriority <> '2-HIGH'
        THEN 1 ELSE 0
    END) as low_line_count
FROM
    orders,
    lineitem
WHERE
    o_orderkey = l_orderkey
    AND l_shipmode IN ('MAIL', 'SHIP')
    AND l_commitdate < l_receiptdate
    AND l_shipdate < l_commitdate
    AND l_receiptdate >= date '1994-01-01'
    AND l_receiptdate < date '1994-01-01' + interval '1 year'
GROUP BY
    l_shipmode
ORDER BY
    l_shipmode;


-- ============================================
-- q13.sql - TPC-H Query q13
-- ============================================
-- TPC-H Query 13: Customer Distribution
-- Tests: LEFT OUTER JOIN, NOT LIKE, nested aggregation
SELECT
    c_count,
    count(*) as custdist
FROM
    (
        SELECT
            c_custkey,
            count(o_orderkey) as c_count
        FROM
            customer LEFT OUTER JOIN orders ON
                c_custkey = o_custkey
                AND o_comment NOT LIKE '%special%requests%'
        GROUP BY
            c_custkey
    ) as c_orders
GROUP BY
    c_count
ORDER BY
    custdist DESC,
    c_count DESC;


-- ============================================
-- q14.sql - TPC-H Query q14
-- ============================================
-- TPC-H Query 14: Promotion Effect
-- Tests: 2-table join, CASE in aggregation
SELECT
    100.00 * sum(CASE
        WHEN p_type LIKE 'PROMO%'
        THEN l_extendedprice * (1 - l_discount)
        ELSE 0
    END) / sum(l_extendedprice * (1 - l_discount)) as promo_revenue
FROM
    lineitem,
    part
WHERE
    l_partkey = p_partkey
    AND l_shipdate >= date '1995-09-01'
    AND l_shipdate < date '1995-09-01' + interval '1 month';


-- ============================================
-- q15.sql - TPC-H Query q15
-- ============================================
-- TPC-H Query 15: Top Supplier
-- Tests: view/CTE, correlated subquery
WITH revenue AS (
    SELECT
        l_suppkey as supplier_no,
        sum(l_extendedprice * (1 - l_discount)) as total_revenue
    FROM
        lineitem
    WHERE
        l_shipdate >= date '1996-01-01'
        AND l_shipdate < date '1996-01-01' + interval '3 month'
    GROUP BY
        l_suppkey
)
SELECT
    s_suppkey,
    s_name,
    s_address,
    s_phone,
    total_revenue
FROM
    supplier,
    revenue
WHERE
    s_suppkey = supplier_no
    AND total_revenue = (
        SELECT max(total_revenue) FROM revenue
    )
ORDER BY
    s_suppkey;


-- ============================================
-- q16.sql - TPC-H Query q16
-- ============================================
-- TPC-H Query 16: Parts/Supplier Relationship
-- Tests: NOT IN subquery, NOT LIKE, IN list
SELECT
    p_brand,
    p_type,
    p_size,
    count(DISTINCT ps_suppkey) as supplier_cnt
FROM
    partsupp,
    part
WHERE
    p_partkey = ps_partkey
    AND p_brand <> 'Brand#45'
    AND p_type NOT LIKE 'MEDIUM POLISHED%'
    AND p_size IN (49, 14, 23, 45, 19, 3, 36, 9)
    AND ps_suppkey NOT IN (
        SELECT s_suppkey
        FROM supplier
        WHERE s_comment LIKE '%Customer%Complaints%'
    )
GROUP BY
    p_brand,
    p_type,
    p_size
ORDER BY
    supplier_cnt DESC,
    p_brand,
    p_type,
    p_size;


-- ============================================
-- q17.sql - TPC-H Query q17
-- ============================================
-- TPC-H Query 17: Small-Quantity-Order Revenue
-- Tests: correlated subquery with aggregation
SELECT
    sum(l_extendedprice) / 7.0 as avg_yearly
FROM
    lineitem,
    part
WHERE
    p_partkey = l_partkey
    AND p_brand = 'Brand#23'
    AND p_container = 'MED BOX'
    AND l_quantity < (
        SELECT 0.2 * avg(l_quantity)
        FROM lineitem
        WHERE l_partkey = p_partkey
    );


-- ============================================
-- q18.sql - TPC-H Query q18
-- ============================================
-- TPC-H Query 18: Large Volume Customer
-- Tests: IN subquery with HAVING, multi-table join
SELECT
    c_name,
    c_custkey,
    o_orderkey,
    o_orderdate,
    o_totalprice,
    sum(l_quantity)
FROM
    customer,
    orders,
    lineitem
WHERE
    o_orderkey IN (
        SELECT l_orderkey
        FROM lineitem
        GROUP BY l_orderkey
        HAVING sum(l_quantity) > 300
    )
    AND c_custkey = o_custkey
    AND o_orderkey = l_orderkey
GROUP BY
    c_name,
    c_custkey,
    o_orderkey,
    o_orderdate,
    o_totalprice
ORDER BY
    o_totalprice DESC,
    o_orderdate
LIMIT 100;


-- ============================================
-- q19.sql - TPC-H Query q19
-- ============================================
-- TPC-H Query 19: Discounted Revenue
-- Tests: complex OR conditions, range predicates
SELECT
    sum(l_extendedprice * (1 - l_discount)) as revenue
FROM
    lineitem,
    part
WHERE
    (
        p_partkey = l_partkey
        AND p_brand = 'Brand#12'
        AND p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
        AND l_quantity >= 1 AND l_quantity <= 1 + 10
        AND p_size BETWEEN 1 AND 5
        AND l_shipmode IN ('AIR', 'AIR REG')
        AND l_shipinstruct = 'DELIVER IN PERSON'
    )
    OR
    (
        p_partkey = l_partkey
        AND p_brand = 'Brand#23'
        AND p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
        AND l_quantity >= 10 AND l_quantity <= 10 + 10
        AND p_size BETWEEN 1 AND 10
        AND l_shipmode IN ('AIR', 'AIR REG')
        AND l_shipinstruct = 'DELIVER IN PERSON'
    )
    OR
    (
        p_partkey = l_partkey
        AND p_brand = 'Brand#34'
        AND p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
        AND l_quantity >= 20 AND l_quantity <= 20 + 10
        AND p_size BETWEEN 1 AND 15
        AND l_shipmode IN ('AIR', 'AIR REG')
        AND l_shipinstruct = 'DELIVER IN PERSON'
    );


-- ============================================
-- q20.sql - TPC-H Query q20
-- ============================================
-- TPC-H Query 20: Potential Part Promotion
-- Tests: nested IN subqueries
SELECT
    s_name,
    s_address
FROM
    supplier,
    nation
WHERE
    s_suppkey IN (
        SELECT ps_suppkey
        FROM partsupp
        WHERE ps_partkey IN (
            SELECT p_partkey
            FROM part
            WHERE p_name LIKE 'forest%'
        )
        AND ps_availqty > (
            SELECT 0.5 * sum(l_quantity)
            FROM lineitem
            WHERE l_partkey = ps_partkey
            AND l_suppkey = ps_suppkey
            AND l_shipdate >= date '1994-01-01'
            AND l_shipdate < date '1994-01-01' + interval '1 year'
        )
    )
    AND s_nationkey = n_nationkey
    AND n_name = 'CANADA'
ORDER BY
    s_name;


-- ============================================
-- q21.sql - TPC-H Query q21
-- ============================================
-- TPC-H Query 21: Suppliers Who Kept Orders Waiting
-- Tests: EXISTS/NOT EXISTS, multi-table join
SELECT
    s_name,
    count(*) as numwait
FROM
    supplier,
    lineitem l1,
    orders,
    nation
WHERE
    s_suppkey = l1.l_suppkey
    AND o_orderkey = l1.l_orderkey
    AND o_orderstatus = 'F'
    AND l1.l_receiptdate > l1.l_commitdate
    AND EXISTS (
        SELECT *
        FROM lineitem l2
        WHERE l2.l_orderkey = l1.l_orderkey
        AND l2.l_suppkey <> l1.l_suppkey
    )
    AND NOT EXISTS (
        SELECT *
        FROM lineitem l3
        WHERE l3.l_orderkey = l1.l_orderkey
        AND l3.l_suppkey <> l1.l_suppkey
        AND l3.l_receiptdate > l3.l_commitdate
    )
    AND s_nationkey = n_nationkey
    AND n_name = 'SAUDI ARABIA'
GROUP BY
    s_name
ORDER BY
    numwait DESC,
    s_name
LIMIT 100;


-- ============================================
-- q22.sql - TPC-H Query q22
-- ============================================
-- TPC-H Query 22: Global Sales Opportunity
-- Tests: NOT EXISTS, correlated subquery, SUBSTRING
SELECT
    cntrycode,
    count(*) as numcust,
    sum(c_acctbal) as totacctbal
FROM
    (
        SELECT
            substring(c_phone from 1 for 2) as cntrycode,
            c_acctbal
        FROM
            customer
        WHERE
            substring(c_phone from 1 for 2) IN ('13', '31', '23', '29', '30', '18', '17')
            AND c_acctbal > (
                SELECT avg(c_acctbal)
                FROM customer
                WHERE c_acctbal > 0.00
                AND substring(c_phone from 1 for 2) IN ('13', '31', '23', '29', '30', '18', '17')
            )
            AND NOT EXISTS (
                SELECT *
                FROM orders
                WHERE o_custkey = c_custkey
            )
    ) as custsale
GROUP BY
    cntrycode
ORDER BY
    cntrycode;


