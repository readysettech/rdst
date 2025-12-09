# TPC-H SF10 Benchmark Testing Guide for RDST

This guide documents how to set up and run TPC-H Scale Factor 10 benchmarks to test RDST Analyze query optimization recommendations.

## Overview

TPC-H is an industry-standard decision support benchmark with 22 complex analytical queries. We use Scale Factor 10 (~10GB of data, ~86M total rows) to test RDST's ability to:
- Analyze complex multi-table joins
- Recommend appropriate indexes
- Suggest query rewrites
- Generate caveats about index trade-offs

## Requirements

- **Disk Space**: ~15GB (3.1GB dump + ~12GB when loaded)
- **Memory**: 4GB+ recommended for PostgreSQL
- **Docker**: Required for easy setup
- **RDST**: Installed and configured
- **Anthropic API Key**: For LLM analysis

## Quick Start (5 minutes)

### Option 1: Download Pre-built Dump from S3 (Recommended)

```bash
# 1. Create working directory
mkdir -p ~/tpch-postgres && cd ~/tpch-postgres

# 2. Download the pre-built dump (3.1GB)
aws s3 cp s3://readysetdbbackups/tpch/sf10/postgres/testdb.dump ./testdb.dump

# 3. Start PostgreSQL container
docker run -d --name tpch-postgres \
  -e POSTGRES_PASSWORD=tpchtest \
  -p 5433:5432 \
  -v tpch-pgdata:/var/lib/postgresql/data \
  -v $(pwd):/backup \
  postgres:15

# 4. Wait for PostgreSQL to start, then restore
sleep 5
docker exec tpch-postgres createdb -U postgres testdb
docker exec tpch-postgres pg_restore -U postgres -d testdb -j 4 /backup/testdb.dump

# 5. Verify (should show ~86M total rows)
PGPASSWORD=tpchtest psql -h localhost -p 5433 -U postgres -d testdb \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"
```

### Option 2: Generate from Scratch (30-60 minutes)

If you need to regenerate the data, follow the PostgreSQL Wiki guide:

```bash
# 1. Clone TPC-H dbgen tool
git clone https://github.com/gregrahn/tpch-kit.git
cd tpch-kit/dbgen

# 2. Build for PostgreSQL
make MACHINE=LINUX DATABASE=POSTGRESQL

# 3. Generate SF10 data (creates ~11GB of CSV files)
./dbgen -s 10

# 4. Start PostgreSQL and create schema
# See: https://wiki.postgresql.org/wiki/TPC-H for schema DDL

# 5. Load data using COPY commands
```

Reference: https://wiki.postgresql.org/wiki/TPC-H

## Configure RDST Target

```bash
# Set environment variables
export TPCH_POSTGRES_PASSWORD="tpchtest"
export ANTHROPIC_API_KEY="your-api-key"

# Add the target
cd /path/to/rdst
python3 rdst.py configure add \
  --target tpch-local \
  --engine postgresql \
  --host localhost \
  --port 5433 \
  --user postgres \
  --password-env TPCH_POSTGRES_PASSWORD \
  --database testdb

# Verify connection
python3 rdst.py configure test --target tpch-local
```

## Run Sample Analyses

### Simple Query (verify setup)
```bash
python3 rdst.py analyze --target tpch-local \
  --query "SELECT * FROM nation LIMIT 5"
```

### Complex Query (Q3 - Shipping Priority)
```bash
python3 rdst.py analyze --target tpch-local --query "
SELECT l_orderkey, sum(l_extendedprice * (1 - l_discount)) as revenue,
       o_orderdate, o_shippriority
FROM customer, orders, lineitem
WHERE c_mktsegment = 'BUILDING'
  AND c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate < date '1995-03-15'
  AND l_shipdate > date '1995-03-15'
GROUP BY l_orderkey, o_orderdate, o_shippriority
ORDER BY revenue DESC, o_orderdate
LIMIT 10"
```

### Full Table Scan Query (tests index recommendations)
```bash
python3 rdst.py analyze --target tpch-local \
  --query "SELECT p_partkey, p_name FROM part WHERE p_name LIKE '%green%'"
```

## Dataset Details

### Table Row Counts (SF10)

| Table     | Rows       | Size  |
|-----------|------------|-------|
| lineitem  | 59,986,052 | 7.4GB |
| orders    | 15,000,000 | 1.7GB |
| partsupp  |  8,000,000 | 1.1GB |
| part      |  2,000,000 | 262MB |
| customer  |  1,500,000 | 236MB |
| supplier  |    100,000 | 15MB  |
| nation    |         25 | 4KB   |
| region    |          5 | 4KB   |

### Primary Keys Only (No Secondary Indexes)

The database is loaded **without secondary indexes** to test RDST's index recommendation capabilities. Only primary key constraints exist:
- `lineitem_pkey (l_orderkey, l_linenumber)`
- `orders_pkey (o_orderkey)`
- `partsupp_pkey (ps_partkey, ps_suppkey)`
- `part_pkey (p_partkey)`
- `customer_pkey (c_custkey)`
- `supplier_pkey (s_suppkey)`
- `nation_pkey (n_nationkey)`
- `region_pkey (r_regionkey)`

## What We Learned (December 2025)

### Index Recommendation Quality

1. **Existing index awareness**: RDST correctly identifies when existing indexes already cover query needs
2. **Caveats feature**: LLM now provides important warnings:
   - `workload_context`: Single-query optimization may not be optimal for full workload
   - `planner_impact`: Too many indexes can confuse query planner
   - `btree_limitations`: B-tree can't help with leading wildcards

3. **GIN trigram indexes**: For LIKE '%pattern%' queries, RDST correctly recommends GIN with pg_trgm

### Holistic vs Single-Query Analysis

Testing revealed that analyzing queries individually may suggest conflicting indexes. The `--workload` flag (coming soon) addresses this by:
- Analyzing multiple queries together
- Identifying index consolidation opportunities
- Avoiding redundant indexes

### Performance Characteristics

- Simple queries (indexed lookups): ~1ms execution, excellent ratings
- Complex multi-join queries: 1-30s execution depending on selectivity
- Full table scans on lineitem (60M rows): Can exceed 10s

## TPC-H Query Reference

| Query | Type | Key Features |
|-------|------|--------------|
| Q1  | Aggregation | Date filtering, SUM/AVG/COUNT |
| Q2  | Correlated subquery | Minimum cost supplier |
| Q3  | 3-table join | Shipping priority |
| Q4  | EXISTS | Order priority checking |
| Q5  | 6-table join | Local supplier volume |
| Q6  | Simple scan | Range predicates, revenue forecast |
| Q7  | OR conditions | Volume shipping, multi-join |
| Q8  | 8-table join | Market share, CASE expressions |
| Q9  | LIKE pattern | Product profit, 6-table join |
| Q10 | Date range | Returned items reporting |
| Q11 | HAVING subquery | Stock identification |
| Q12 | CASE aggregation | Shipping mode analysis |
| Q13 | LEFT OUTER JOIN | Customer distribution |
| Q14 | CASE in aggregation | Promotion effect |
| Q15 | CTE | Top supplier revenue |
| Q16 | NOT IN/NOT LIKE | Parts/supplier filtering |
| Q17 | Correlated subquery | Small quantity revenue |
| Q18 | IN with HAVING | Large volume customers |
| Q19 | Complex OR | Discounted revenue |
| Q20 | Nested IN | Part promotion analysis |
| Q21 | EXISTS/NOT EXISTS | Waiting suppliers |
| Q22 | NOT EXISTS | Global sales opportunity |

## Cleanup

```bash
# Stop and remove container
docker stop tpch-postgres && docker rm tpch-postgres

# Remove volume (deletes data)
docker volume rm tpch-pgdata

# Remove dump file
rm ~/tpch-postgres/testdb.dump
```

## References

- [TPC-H Specification](http://tpc.org/tpch/)
- [PostgreSQL TPC-H Wiki](https://wiki.postgresql.org/wiki/TPC-H)
- [gregrahn/tpch-kit](https://github.com/gregrahn/tpch-kit)
- [ankane/tpch-kit](https://github.com/ankane/tpch-kit) - Modern fork with Docker support
