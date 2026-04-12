"""Shared ORM detection patterns for codebase scanning.

Used by both ScanCommand (CLI) and ScanService (Web API) for
file discovery and ORM type filtering.
"""

import re
from typing import Dict, List, Pattern, Set

# File extensions for JS/TS files (use JS extractor)
_JS_EXTENSIONS: Set[str] = {".js", ".ts", ".tsx", ".jsx"}

# All scannable file extensions
_SCAN_EXTENSIONS: Set[str] = {".py"} | _JS_EXTENSIONS

# ORM detection patterns — comprehensive coverage
ORM_PATTERNS: Dict[str, List[str]] = {
    "sqlalchemy": [
        # Imports
        r"from sqlalchemy",
        r"import sqlalchemy",
        # Session/Query patterns (1.x style)
        r"\.query\(",
        r"\.filter\(",
        r"\.filter_by\(",
        r"\.join\(",
        r"\.outerjoin\(",
        r"\.group_by\(",
        r"\.order_by\(",
        r"\.having\(",
        r"\.distinct\(",
        r"\.limit\(",
        r"\.offset\(",
        r"\.subquery\(",
        r"\.with_entities\(",
        r"\.options\(",
        r"\.correlate\(",
        r"\.union\(",
        r"\.union_all\(",
        r"\.intersect\(",
        r"\.except_\(",
        # Terminal methods
        r"\.all\(\)",
        r"\.first\(\)",
        r"\.one\(\)",
        r"\.one_or_none\(\)",
        r"\.scalar\(",
        r"\.scalars\(",
        r"\.count\(\)",
        r"\.exists\(\)",
        r"\.fetchall\(",
        r"\.fetchone\(",
        r"\.fetchmany\(",
        # SQLAlchemy 2.0 style
        r"\bselect\(",
        r"\binsert\(",
        r"\bupdate\(",
        r"\bdelete\(",
        r"\.execute\(",
        # Session operations
        r"session\.(query|execute|add|delete|commit|flush|merge|refresh)",
        r"db\.(query|execute|session|add|commit)",
        # Relationship loading
        r"joinedload\(",
        r"subqueryload\(",
        r"selectinload\(",
        r"lazyload\(",
        r"immediateload\(",
        # Raw SQL
        r"text\(['\"]",
        # Functions
        r"func\.\w+\(",
        r"and_\(",
        r"or_\(",
        r"not_\(",
        r"case\(",
        r"cast\(",
        r"coalesce\(",
        r"nullif\(",
        r"literal\(",
        r"desc\(",
        r"asc\(",
        r"nullsfirst\(",
        r"nullslast\(",
    ],
    "django": [
        # QuerySet creation
        r"\.objects\.",
        # Filtering
        r"\.filter\(",
        r"\.exclude\(",
        r"\.get\(",
        # Terminal methods
        r"\.all\(\)",
        r"\.first\(\)",
        r"\.last\(\)",
        r"\.latest\(",
        r"\.earliest\(",
        r"\.count\(\)",
        r"\.exists\(\)",
        r"\.iterator\(",
        # Aggregation
        r"\.annotate\(",
        r"\.aggregate\(",
        # Related objects
        r"\.select_related\(",
        r"\.prefetch_related\(",
        # Output transformation
        r"\.values\(",
        r"\.values_list\(",
        r"\.only\(",
        r"\.defer\(",
        # Ordering/Distinct
        r"\.order_by\(",
        r"\.reverse\(\)",
        r"\.distinct\(",
        # Bulk operations
        r"\.update\(",
        r"\.delete\(",
        r"\.create\(",
        r"\.bulk_create\(",
        r"\.bulk_update\(",
        r"\.get_or_create\(",
        r"\.update_or_create\(",
        r"\.in_bulk\(",
        # Raw SQL
        r"\.raw\(",
        r"\.extra\(",
        r"RawSQL\(",
        # Expressions
        r"\bF\(['\"]",
        r"\bQ\(",
        r"\bValue\(",
        r"\bCase\(",
        r"\bWhen\(",
        r"\bSubquery\(",
        r"\bExists\(",
        r"\bOuterRef\(",
        # Aggregate functions
        r"\bSum\(",
        r"\bCount\(",
        r"\bAvg\(",
        r"\bMin\(",
        r"\bMax\(",
        r"\bStdDev\(",
        r"\bVariance\(",
        # Window functions
        r"\.window\(",
        r"\bWindow\(",
        r"\bRowNumber\(",
        r"\bRank\(",
        r"\bDenseRank\(",
        # Lookups (used in filter kwargs)
        r"__exact=",
        r"__iexact=",
        r"__contains=",
        r"__icontains=",
        r"__in=",
        r"__gt=",
        r"__gte=",
        r"__lt=",
        r"__lte=",
        r"__startswith=",
        r"__istartswith=",
        r"__endswith=",
        r"__iendswith=",
        r"__range=",
        r"__isnull=",
        r"__regex=",
        r"__iregex=",
    ],
    "raw_sql": [
        r"execute\(['\"]SELECT",
        r"execute\(['\"]INSERT",
        r"execute\(['\"]UPDATE",
        r"execute\(['\"]DELETE",
        r"cursor\.execute\(",
        r"text\(['\"]SELECT",
        r"\.executemany\(",
        r"connection\.cursor\(",
    ],
    "prisma": [
        # Imports / client
        r"@prisma/client",
        r"PrismaClient",
        r"prisma\.\w+\.",
        # Query methods
        r"\.findMany\(",
        r"\.findUnique\(",
        r"\.findFirst\(",
        r"\.findFirstOrThrow\(",
        r"\.findUniqueOrThrow\(",
        # Mutations
        r"\.createMany\(",
        r"\.createManyAndReturn\(",
        r"\.updateMany\(",
        r"\.updateManyAndReturn\(",
        r"\.upsert\(",
        r"\.deleteMany\(",
        # Aggregation
        r"\.aggregate\(",
        r"\.groupBy\(",
        # Raw SQL
        r"\.\$queryRaw",
        r"\.\$queryRawUnsafe\(",
        r"\.\$executeRaw",
        r"\.\$executeRawUnsafe\(",
        # Transaction
        r"\.\$transaction\(",
        # Prisma-specific args
        r"\binclude\s*:",
        r"\bwhere\s*:",
        r"\borderBy\s*:",
        r"\btake\s*:",
        r"\bskip\s*:",
        r"\bdistinct\s*:",
    ],
    "drizzle": [
        # Imports
        r"drizzle-orm",
        r"from ['\"]drizzle-",
        # Builder starters
        r"\bdb\.select\(",
        r"\bdb\.selectDistinct\(",
        r"\bdb\.selectDistinctOn\(",
        r"\bdb\.insert\(",
        r"\bdb\.update\(",
        r"\bdb\.delete\(",
        r"\bdb\.execute\(",
        r"\bdb\.\$count\(",
        # Relational API
        r"\bdb\.query\.\w+\.",
        # Chain methods
        r"\.from\(",
        r"\.innerJoin\(",
        r"\.leftJoin\(",
        r"\.rightJoin\(",
        r"\.fullJoin\(",
        r"\.groupBy\(",
        r"\.having\(",
        r"\.orderBy\(",
        r"\.limit\(",
        r"\.offset\(",
        r"\.returning\(",
        r"\.onConflictDoNothing\(",
        r"\.onConflictDoUpdate\(",
        r"\.onDuplicateKeyUpdate\(",
        r"\.values\(",
        r"\.set\(",
        # Drizzle operators
        r"\beq\(",
        r"\bne\(",
        r"\bgt\(",
        r"\bgte\(",
        r"\blt\(",
        r"\blte\(",
        r"\blike\(",
        r"\bilike\(",
        r"\binArray\(",
        r"\bnotInArray\(",
        r"\bisNull\(",
        r"\bisNotNull\(",
        r"\bbetween\(",
        r"\band\(",
        r"\bor\(",
        r"\bnot\(",
        # Drizzle aggregate/functions
        r"\bcount\(",
        r"\bsum\(",
        r"\bavg\(",
        r"\bmin\(",
        r"\bmax\(",
        r"\bcountDistinct\(",
        # Raw SQL tag
        r"\bsql`",
        r"\bsql\.raw\(",
        # Transaction / batch
        r"\bdb\.transaction\(",
        r"\bdb\.batch\(",
        # Set operations
        r"\bunion\(",
        r"\bunionAll\(",
        r"\bintersect\(",
        r"\bexcept\(",
    ],
}

# Pre-compiled patterns for performance when scanning many files
ORM_PATTERNS_COMPILED: Dict[str, List[Pattern[str]]] = {
    orm_name: [re.compile(p, re.IGNORECASE) for p in patterns]
    for orm_name, patterns in ORM_PATTERNS.items()
}
