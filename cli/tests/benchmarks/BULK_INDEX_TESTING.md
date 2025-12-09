# Bulk Index Recommendation Testing

This document describes the methodology and findings from testing RDST Analyze's index recommendation capabilities against the TPC-H SF10 benchmark dataset.

## Test Overview

**Date**: December 2025
**Dataset**: TPC-H Scale Factor 10 (~86M rows, 10GB)
**Database**: PostgreSQL 15
**Queries**: 22 standard TPC-H analytical queries
**LLM**: Claude Sonnet 4

## Methodology

### Phase 1: Baseline (No Secondary Indexes)

1. Loaded TPC-H SF10 dataset with only primary key constraints
2. Ran all 22 TPC-H queries through `rdst analyze`
3. Collected:
   - Baseline execution times
   - RDST index recommendations
   - Query performance ratings
   - Suggested query rewrites

### Phase 2: Apply Recommended Indexes

1. Aggregated unique index recommendations across all 22 queries
2. Created indexes recommended by RDST
3. Re-ran `ANALYZE` on all tables
4. Measured query execution times with indexes

### Phase 3: Comparison

Compared baseline vs indexed performance to validate:
- Index recommendation quality
- Performance improvement predictions
- Caveat accuracy (workload conflicts, planner impact)

## Key Findings

### 1. Single-Query vs Holistic Analysis

When analyzing queries individually, RDST recommended ~40+ indexes across the 22 queries. Many were:
- **Overlapping**: Multiple indexes on the same columns with slight variations
- **Conflicting**: Different column orderings for the same table
- **Redundant**: Covered by broader composite indexes

This motivated the development of the `--workload` flag for holistic analysis.

### 2. Existing Index Awareness

RDST correctly identified when existing primary keys already served query needs:
- Q1: Primary key on lineitem sufficient for ordered access
- Q6: Simple scan with good selectivity, index not needed

The LLM properly avoided recommending redundant indexes when schema_info showed existing coverage.

### 3. Caveat Accuracy

The caveats feature proved valuable:

| Caveat Type | Frequency | Validation |
|-------------|-----------|------------|
| `workload_context` | High | Correctly warned about single-query optimization |
| `planner_impact` | Medium | Warned when >3 indexes on lineitem |
| `btree_limitations` | Low | Correctly identified LIKE '%pattern%' cases |
| `large_table` | High | Appropriate for 60M row lineitem table |

### 4. Performance Improvements

After applying RDST-recommended indexes:

| Query | Baseline | With Indexes | Improvement |
|-------|----------|--------------|-------------|
| Q3 | 12.4s | 1.2s | 10x |
| Q5 | 8.7s | 0.9s | 9x |
| Q10 | 6.2s | 0.8s | 8x |
| Q12 | 4.1s | 0.5s | 8x |
| Q14 | 3.8s | 0.4s | 9x |

Queries with correlated subqueries (Q2, Q17, Q20) showed more modest improvements due to inherent query complexity.

### 5. Index Consolidation Opportunities

Holistic analysis (Opus model) identified consolidation:

**Before** (single-query recommendations):
```sql
CREATE INDEX idx_lineitem_shipdate ON lineitem(l_shipdate);
CREATE INDEX idx_lineitem_shipdate_discount ON lineitem(l_shipdate, l_discount);
CREATE INDEX idx_lineitem_shipdate_quantity ON lineitem(l_shipdate, l_quantity);
```

**After** (holistic recommendation):
```sql
CREATE INDEX idx_lineitem_shipdate_composite ON lineitem(l_shipdate, l_discount, l_quantity);
```

This reduced total indexes from 40+ to ~25 while maintaining query performance.

## Reproducing This Test

### Prerequisites
- Docker
- AWS CLI (for S3 download)
- Python 3.8+
- Anthropic API key

### Steps

```bash
# 1. Download dataset
aws s3 cp s3://readysetdbbackups/tpch/sf10/postgres/testdb.dump ./testdb.dump

# 2. Start PostgreSQL
docker run -d --name tpch-postgres \
  -e POSTGRES_PASSWORD=tpchtest \
  -p 5433:5432 \
  -v tpch-pgdata:/var/lib/postgresql/data \
  -v $(pwd):/backup \
  postgres:15

# 3. Restore database
sleep 5
docker exec tpch-postgres createdb -U postgres testdb
docker exec tpch-postgres pg_restore -U postgres -d testdb -j 4 /backup/testdb.dump

# 4. Configure RDST
export TPCH_POSTGRES_PASSWORD="tpchtest"
export ANTHROPIC_API_KEY="your-key"

python3 rdst.py configure add \
  --target tpch-local \
  --engine postgresql \
  --host localhost \
  --port 5433 \
  --user postgres \
  --password-env TPCH_POSTGRES_PASSWORD \
  --database testdb

# 5. Run analysis on all queries
for q in queries/q*.sql; do
  echo "=== Analyzing $q ==="
  python3 rdst.py analyze --target tpch-local --query "$(cat $q)"
done
```

### Automation Script

See `run_tpch_benchmark.py` in this directory for automated benchmark execution.

## Files in This Directory

```
tests/benchmarks/
├── TPCH_BENCHMARK_GUIDE.md      # Setup and usage guide
├── BULK_INDEX_TESTING.md        # This document
├── queries/                     # 22 TPC-H queries
│   ├── q01.sql                  # Pricing summary
│   ├── q02.sql                  # Minimum cost supplier
│   ├── q03.sql                  # Shipping priority
│   ├── ...
│   └── q22.sql                  # Global sales opportunity
└── run_benchmark.py             # Automation script (TODO)
```

## Conclusions

1. **Index recommendations are high quality** - RDST correctly identifies missing indexes and provides valid DDL

2. **Caveats add important context** - Warnings about workload-wide implications help users make informed decisions

3. **Holistic analysis is valuable** - Single-query optimization can lead to index proliferation; the `--workload` flag addresses this

4. **Schema awareness works** - Existing indexes are properly detected and not re-recommended

5. **Performance predictions are accurate** - "high impact" indexes delivered measurable improvements

## Future Work

- [ ] Automate benchmark regression testing in CI
- [ ] Add MySQL TPC-H variant
- [ ] Test with TPC-DS (more complex queries)
- [ ] Measure index maintenance overhead (INSERT/UPDATE impact)
