# Implementation Plan: CLI/Web Feature Parity

## Executive Summary

The RDST CLI `analyze` command has significantly more features than the Web API. This plan outlines how to achieve feature parity by:

1. **Reusing existing business logic** in `/rdst/lib/functions/`
2. **Creating a shared AnalysisService** that both CLI and Web can use
3. **Keeping rendering logic separate** (CLI uses Rich, Web returns JSON)

## Current State Analysis

### CLI Features (analyze_command.py)
| Feature | Status | Implementation |
|---------|--------|----------------|
| Query validation | ✅ | `validate_query_safety()` |
| Parameter detection | ✅ | `query_parameterization.py` |
| EXPLAIN ANALYZE | ✅ | `execute_explain_analyze()` |
| Schema collection | ✅ | `collect_target_schema()` |
| LLM analysis | ✅ | `analyze_with_llm()` |
| Rewrite testing | ✅ | `test_query_rewrites()` |
| Readyset cacheability | ✅ | `check_readyset_cacheability()` |
| Query registry | ✅ | `store_analysis_results()` |
| Formatted output | ✅ | `format_analysis_output()` → `output_formatter.py` |

### Web API Features (routes/analyze.py)
| Feature | Status | Gap |
|---------|--------|-----|
| Query validation | ❌ | Not implemented |
| Parameter detection | ❌ | Fails on $1, ? placeholders |
| EXPLAIN ANALYZE | ✅ | Working |
| Schema collection | ❌ | Not called - LLM lacks context |
| LLM analysis | ✅ | Working but missing schema |
| Rewrite testing | ❌ | Returns suggestions only |
| Readyset cacheability | ❌ | Not implemented |
| Query registry | ❌ | Not implemented |
| Formatted output | ⚠️ | Returns raw dicts, not structured |

## Architecture Target

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            TARGET ARCHITECTURE                                │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   CLI (analyze_command.py)              Web API (routes/analyze.py)          │
│   ┌─────────────────────────┐           ┌────────────────────────┐           │
│   │ • Parse CLI args        │           │ • Parse HTTP request   │           │
│   │ • Resolve input         │           │ • SSE streaming        │           │
│   │ • Call AnalysisService  │           │ • Call AnalysisService │           │
│   │ • Render with Rich      │           │ • Return JSON          │           │
│   └───────────┬─────────────┘           └───────────┬────────────┘           │
│               │                                      │                        │
│               └──────────────┬───────────────────────┘                        │
│                              ▼                                                │
│               ┌──────────────────────────────┐                                │
│               │      AnalysisService         │  ← NEW                         │
│               │  /rdst/lib/services/         │                                │
│               │                              │                                │
│               │  • run_full_analysis()       │                                │
│               │  • run_quick_analysis()      │                                │
│               │  • on_progress callback      │                                │
│               │  • Returns structured dict   │                                │
│               └──────────────┬───────────────┘                                │
│                              │                                                │
│                              ▼                                                │
│               ┌──────────────────────────────────────────────────────────────┐│
│               │                 /rdst/lib/functions/                         ││
│               │  (Unchanged - already pure business logic)                   ││
│               │                                                               ││
│               │  execute_explain_analyze() → Dict                            ││
│               │  analyze_with_llm() → Dict                                   ││
│               │  test_query_rewrites() → Dict                                ││
│               │  check_readyset_cacheability() → Dict                        ││
│               │  collect_target_schema() → Dict                              ││
│               │  format_analysis_output() → Dict                             ││
│               └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Create AnalysisService

**Goal**: Extract a reusable service that orchestrates the full analysis pipeline.

### File: `/rdst/lib/services/__init__.py`
```python
from .analysis_service import AnalysisService

__all__ = ["AnalysisService"]
```

### File: `/rdst/lib/services/analysis_service.py`

```python
"""
AnalysisService - Shared analysis orchestration for CLI and Web.

This service encapsulates the full analysis pipeline, calling functions from
/rdst/lib/functions/ in the correct order. Both CLI and Web API use this
service, but render the results differently.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import time


class AnalysisStage(Enum):
    """Stages of the analysis pipeline for progress reporting."""
    VALIDATING = ("validating", 5, "Validating query safety...")
    NORMALIZING = ("normalizing", 10, "Normalizing query...")
    EXECUTING_EXPLAIN = ("executing_explain", 20, "Running EXPLAIN ANALYZE...")
    COLLECTING_SCHEMA = ("collecting_schema", 35, "Collecting schema info...")
    ANALYZING_LLM = ("analyzing_llm", 50, "Analyzing with AI...")
    TESTING_REWRITES = ("testing_rewrites", 70, "Testing query rewrites...")
    CHECKING_READYSET = ("checking_readyset", 85, "Checking Readyset cacheability...")
    STORING_RESULTS = ("storing_results", 95, "Storing results...")
    COMPLETE = ("complete", 100, "Analysis complete")

    def __init__(self, stage_id: str, percent: int, message: str):
        self.stage_id = stage_id
        self.percent = percent
        self.message = message


@dataclass
class AnalysisRequest:
    """Input parameters for analysis."""
    query: str
    target: str
    target_config: Dict[str, Any]
    
    # Options
    fast_mode: bool = False
    skip_rewrite_testing: bool = False
    skip_readyset_check: bool = False
    skip_storage: bool = False
    llm_model: Optional[str] = None
    
    # For parameter resolution (web UI can provide these)
    parameter_values: Dict[str, Any] = field(default_factory=dict)


@dataclass  
class AnalysisResult:
    """Structured output from analysis."""
    success: bool
    
    # Core results
    explain_results: Dict[str, Any] = field(default_factory=dict)
    llm_analysis: Dict[str, Any] = field(default_factory=dict)
    rewrite_testing: Dict[str, Any] = field(default_factory=dict)
    readyset_cacheability: Dict[str, Any] = field(default_factory=dict)
    
    # Metadata
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    target: str = ""
    database_engine: str = ""
    
    # Formatted output (from format_analysis_output)
    formatted: Dict[str, Any] = field(default_factory=dict)
    
    # Error info
    error: Optional[str] = None
    failed_stage: Optional[str] = None


# Type alias for progress callback
ProgressCallback = Callable[[AnalysisStage, Optional[str]], None]


class AnalysisService:
    """
    Orchestrates the full query analysis pipeline.
    
    Usage:
        service = AnalysisService()
        result = await service.run_analysis(request, on_progress=my_callback)
    """
    
    def __init__(self):
        # Lazy imports to avoid circular dependencies
        self._functions_loaded = False
    
    def _load_functions(self):
        """Lazy load functions to avoid import cycles."""
        if self._functions_loaded:
            return
            
        from ..functions import (
            validate_query_safety,
            parameterize_for_llm,
            normalize_for_registry,
            execute_explain_analyze,
            collect_query_metrics,
            collect_target_schema,
            analyze_with_llm,
            test_query_rewrites,
            check_readyset_cacheability,
            store_analysis_results,
            format_analysis_output,
        )
        
        self.validate_query_safety = validate_query_safety
        self.parameterize_for_llm = parameterize_for_llm
        self.normalize_for_registry = normalize_for_registry
        self.execute_explain_analyze = execute_explain_analyze
        self.collect_query_metrics = collect_query_metrics
        self.collect_target_schema = collect_target_schema
        self.analyze_with_llm = analyze_with_llm
        self.test_query_rewrites = test_query_rewrites
        self.check_readyset_cacheability = check_readyset_cacheability
        self.store_analysis_results = store_analysis_results
        self.format_analysis_output = format_analysis_output
        
        self._functions_loaded = True
    
    async def run_analysis(
        self,
        request: AnalysisRequest,
        on_progress: Optional[ProgressCallback] = None
    ) -> AnalysisResult:
        """
        Run the full analysis pipeline.
        
        Args:
            request: Analysis parameters
            on_progress: Optional callback for progress updates
            
        Returns:
            AnalysisResult with all analysis data
        """
        self._load_functions()
        
        def report(stage: AnalysisStage, detail: Optional[str] = None):
            if on_progress:
                on_progress(stage, detail)
        
        result = AnalysisResult(success=False, target=request.target)
        context = {}  # Accumulates results like WorkflowManager
        
        try:
            # Stage 1: Validate query safety
            report(AnalysisStage.VALIDATING)
            safety = await asyncio.to_thread(
                self.validate_query_safety,
                sql=request.query
            )
            if not safety.get("safe", True):
                result.error = safety.get("reason", "Query failed safety check")
                result.failed_stage = "validating"
                return result
            context["safety_check"] = safety
            
            # Stage 2: Normalize for registry
            report(AnalysisStage.NORMALIZING)
            registry_norm = await asyncio.to_thread(
                self.normalize_for_registry,
                sql=request.query
            )
            context["registry_normalization"] = registry_norm
            result.query_hash = registry_norm.get("hash")
            
            # Also parameterize for LLM (privacy)
            llm_param = await asyncio.to_thread(
                self.parameterize_for_llm,
                sql=request.query
            )
            context["llm_parameterization"] = llm_param
            
            # Stage 3: Execute EXPLAIN ANALYZE
            report(AnalysisStage.EXECUTING_EXPLAIN)
            explain = await asyncio.to_thread(
                self.execute_explain_analyze,
                sql=request.query,
                target=request.target,
                target_config=request.target_config,
                fast_mode=request.fast_mode
            )
            context["explain_results"] = explain
            result.explain_results = explain
            result.database_engine = explain.get("database_engine", "")
            
            if not explain.get("success"):
                result.error = explain.get("error", "EXPLAIN ANALYZE failed")
                result.failed_stage = "executing_explain"
                # Continue anyway - LLM can still provide advice
            
            # Stage 4: Collect query metrics
            metrics = await asyncio.to_thread(
                self.collect_query_metrics,
                sql=request.query,
                target=request.target,
                query_hash=result.query_hash
            )
            context["query_metrics"] = metrics
            
            # Stage 5: Collect schema
            report(AnalysisStage.COLLECTING_SCHEMA)
            schema = await asyncio.to_thread(
                self.collect_target_schema,
                sql=request.query,
                target=request.target,
                target_config=request.target_config
            )
            context["schema_collection"] = schema
            
            # Stage 6: LLM Analysis
            report(AnalysisStage.ANALYZING_LLM)
            llm = await asyncio.to_thread(
                self.analyze_with_llm,
                explain_results=explain,
                query_metrics=metrics,
                parameterized_sql=llm_param.get("parameterized_sql", request.query),
                original_sql=request.query,
                schema_info=schema.get("schema_info", ""),
                model=request.llm_model
            )
            context["llm_analysis"] = llm
            result.llm_analysis = llm
            
            # Stage 7: Test rewrites (optional)
            if not request.skip_rewrite_testing and llm.get("rewrite_suggestions"):
                report(AnalysisStage.TESTING_REWRITES)
                rewrite_results = await asyncio.to_thread(
                    self.test_query_rewrites,
                    original_sql=request.query,
                    rewrite_suggestions=llm.get("rewrite_suggestions", []),
                    target=request.target,
                    original_sql_for_comparison=request.query,
                    fast_mode=request.fast_mode,
                    baseline_result=explain
                )
                context["rewrite_test_results"] = rewrite_results
                result.rewrite_testing = rewrite_results
            
            # Stage 8: Readyset cacheability (optional)
            if not request.skip_readyset_check:
                report(AnalysisStage.CHECKING_READYSET)
                readyset = await asyncio.to_thread(
                    self.check_readyset_cacheability,
                    query=request.query,
                    query_frequency=metrics.get("execution_count", 0)
                )
                context["readyset_cacheability"] = readyset
                result.readyset_cacheability = readyset
            
            # Stage 9: Store results (optional)
            if not request.skip_storage:
                report(AnalysisStage.STORING_RESULTS)
                storage = await asyncio.to_thread(
                    self.store_analysis_results,
                    query_hash=result.query_hash,
                    query=request.query,
                    target=request.target,
                    explain_results=explain,
                    query_metrics=metrics,
                    llm_analysis=llm,
                    rewrite_test_results=context.get("rewrite_test_results", {}),
                    registry_normalization=registry_norm,
                    llm_parameterization=llm_param
                )
                context["storage_result"] = storage
                result.analysis_id = storage.get("analysis_id")
            
            # Stage 10: Format output
            report(AnalysisStage.COMPLETE)
            formatted = await asyncio.to_thread(
                self.format_analysis_output,
                explain_results=explain,
                llm_analysis=llm,
                rewrite_test_results=context.get("rewrite_test_results", {}),
                readyset_cacheability=context.get("readyset_cacheability", {}),
                query_metrics=metrics,
                query=request.query,
                parameterized_sql=llm_param.get("parameterized_sql", ""),
                normalized_query=registry_norm.get("normalized_sql", ""),
                target=request.target,
                analysis_id=result.analysis_id,
                schema_collection=schema
            )
            result.formatted = formatted
            result.success = True
            
            return result
            
        except Exception as e:
            result.error = str(e)
            result.failed_stage = "unknown"
            return result
    
    async def run_quick_analysis(
        self,
        request: AnalysisRequest,
        on_progress: Optional[ProgressCallback] = None
    ) -> AnalysisResult:
        """
        Run a quick analysis (EXPLAIN + LLM only, no rewrite testing).
        
        Useful for web UI where user wants fast feedback.
        """
        request.skip_rewrite_testing = True
        request.skip_readyset_check = True
        request.skip_storage = True
        return await self.run_analysis(request, on_progress)
```

### Changes to CLI (analyze_command.py)

The CLI can optionally use `AnalysisService` or continue using `WorkflowManager`. For gradual migration:

```python
# In analyze_command.py, add option to use new service:

async def _run_with_service(self, query: str, target: str, target_config: dict, **options):
    """Run analysis using AnalysisService (new path)."""
    from ..services import AnalysisService, AnalysisRequest
    
    service = AnalysisService()
    request = AnalysisRequest(
        query=query,
        target=target,
        target_config=target_config,
        fast_mode=options.get("fast", False),
        llm_model=options.get("model"),
    )
    
    # Progress callback for CLI spinner
    def on_progress(stage, detail):
        self._update_spinner(stage.message)
    
    result = await service.run_analysis(request, on_progress)
    
    # Result.formatted contains the same structure as format_analysis_output()
    # Pass to output_formatter for Rich rendering
    return result
```

---

## Phase 2: Update Web API

**Goal**: Use `AnalysisService` to get full feature parity.

### File: `/rdst/lib/api/routes/analyze.py` (updated)

```python
"""
Query Analysis API Route

Uses AnalysisService to provide the same features as CLI.
"""

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..models import AnalyzeRequest
from ...services import AnalysisService, AnalysisRequest, AnalysisStage

router = APIRouter()


def _serialize_for_json(obj):
    """Convert complex objects to JSON-serializable format."""
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(v) for v in obj]
    elif hasattr(obj, "__dict__"):
        return str(obj)
    return obj


async def _analyze_generator(request: AnalyzeRequest) -> AsyncGenerator[dict, None]:
    """
    SSE generator for analysis with full feature parity.
    
    Events emitted:
    - progress: {stage, percent, message}
    - explain_complete: {explain_results}
    - rewrite_tested: {rewrite_result}  
    - complete: {full structured result}
    - error: {message}
    """
    from ...cli.rdst_cli import TargetsConfig
    
    try:
        # Load target config
        yield {
            "event": "progress",
            "data": json.dumps({
                "stage": "loading_config",
                "percent": 2,
                "message": "Loading configuration..."
            }),
        }
        
        cfg = TargetsConfig()
        cfg.load()
        target_name = request.target or cfg.get_default()
        
        if not target_name:
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "No target specified and no default configured"
                }),
            }
            return
        
        target_config = cfg.get(target_name)
        if not target_config:
            yield {
                "event": "error", 
                "data": json.dumps({"message": f"Target '{target_name}' not found"}),
            }
            return
        
        # Create analysis service and request
        service = AnalysisService()
        analysis_request = AnalysisRequest(
            query=request.query,
            target=target_name,
            target_config=target_config,
            fast_mode=request.fast,
            skip_rewrite_testing=request.skip_rewrites if hasattr(request, 'skip_rewrites') else False,
            skip_readyset_check=request.skip_readyset if hasattr(request, 'skip_readyset') else False,
            llm_model=request.model if hasattr(request, 'model') else None,
        )
        
        # Track last emitted stage for intermediate events
        last_stage = None
        explain_sent = False
        
        def on_progress(stage: AnalysisStage, detail: str = None):
            nonlocal last_stage, explain_sent
            last_stage = stage
        
        # Run analysis in background, poll for progress
        # (Alternative: use asyncio.Queue for real-time updates)
        
        # For simplicity, run with progress callback that captures stages
        result = await service.run_analysis(analysis_request, on_progress=on_progress)
        
        # Emit explain_complete if we have results
        if result.explain_results:
            yield {
                "event": "explain_complete",
                "data": json.dumps(_serialize_for_json(result.explain_results))
            }
        
        # Emit rewrite results if tested
        if result.rewrite_testing and result.rewrite_testing.get("tested"):
            yield {
                "event": "rewrites_tested", 
                "data": json.dumps(_serialize_for_json(result.rewrite_testing))
            }
        
        # Emit readyset results if checked
        if result.readyset_cacheability and result.readyset_cacheability.get("checked"):
            yield {
                "event": "readyset_checked",
                "data": json.dumps(_serialize_for_json(result.readyset_cacheability))
            }
        
        # Final complete event
        if result.success:
            yield {
                "event": "progress",
                "data": json.dumps({"stage": "complete", "percent": 100})
            }
            yield {
                "event": "complete",
                "data": json.dumps(_serialize_for_json({
                    "success": True,
                    "analysis_id": result.analysis_id,
                    "query_hash": result.query_hash,
                    "explain_results": result.explain_results,
                    "llm_analysis": result.llm_analysis,
                    "rewrite_testing": result.rewrite_testing,
                    "readyset_cacheability": result.readyset_cacheability,
                    "formatted": result.formatted,  # Pre-formatted for UI
                }))
            }
        else:
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": result.error,
                    "stage": result.failed_stage,
                    # Still return partial results
                    "partial_results": {
                        "explain_results": _serialize_for_json(result.explain_results),
                        "llm_analysis": _serialize_for_json(result.llm_analysis),
                    }
                })
            }
            
    except Exception as e:
        yield {"event": "error", "data": json.dumps({"message": str(e)})}


@router.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """
    Analyze a SQL query with full feature parity to CLI.
    
    Returns SSE stream with progress updates and final results.
    """
    return EventSourceResponse(_analyze_generator(request))


@router.post("/analyze/quick")
async def analyze_quick(request: AnalyzeRequest):
    """
    Quick analysis - EXPLAIN + LLM only, no rewrite testing.
    
    Faster response for initial feedback.
    """
    # Set flags to skip slow operations
    request.skip_rewrites = True
    request.skip_readyset = True
    return EventSourceResponse(_analyze_generator(request))
```

### File: `/rdst/lib/api/models.py` (updated)

```python
from pydantic import BaseModel
from typing import Optional, Dict, Any


class AnalyzeRequest(BaseModel):
    query: str
    target: Optional[str] = None
    fast: bool = False
    
    # New options for feature parity
    skip_rewrites: bool = False
    skip_readyset: bool = False
    model: Optional[str] = None  # LLM model override
    
    # For parameterized queries - UI can provide values
    parameter_values: Optional[Dict[str, Any]] = None


class AnalyzeResponse(BaseModel):
    """Non-streaming response structure (for reference)."""
    success: bool
    analysis_id: Optional[str] = None
    query_hash: Optional[str] = None
    
    # Structured results
    explain_results: Dict[str, Any] = {}
    llm_analysis: Dict[str, Any] = {}
    rewrite_testing: Dict[str, Any] = {}
    readyset_cacheability: Dict[str, Any] = {}
    
    # Pre-formatted for UI consumption
    formatted: Dict[str, Any] = {}
    
    # Error info
    error: Optional[str] = None
    failed_stage: Optional[str] = None
```

---

## Phase 3: Enhanced Response Structure

**Goal**: Ensure `format_analysis_output()` returns everything the Web UI needs.

### Update `/rdst/lib/functions/workflow_integration.py`

Add LLM info to metadata for cost display:

```python
def format_analysis_output(**kwargs) -> Dict[str, Any]:
    # ... existing code ...
    
    # Enhanced metadata with LLM cost info
    llm_info = None
    if llm_analysis.get("success"):
        token_usage = llm_analysis.get("token_usage", {})
        if token_usage:
            llm_info = {
                "model": llm_analysis.get("llm_model", "unknown"),
                "tokens": token_usage.get("total", 0),
                "cost": token_usage.get("estimated_cost_usd", 0)
            }
    
    output = {
        # ... existing fields ...
        "metadata": {
            "query": query,
            "normalized_query": kwargs.get('normalized_query', ''),
            "parameterized_sql": kwargs.get('parameterized_sql', ''),
            "target": target,
            "analysis_id": analysis_id,
            "database_engine": explain_results.get('database_engine', ''),
            "analyzed_at": explain_results.get('timestamp', ''),
            "llm_info": llm_info,  # NEW: for UI cost display
        }
    }
```

### Response Structure for Web UI

The `formatted` field in the response will have this structure (same as CLI):

```json
{
  "success": true,
  "message": "Query analysis completed successfully (ID: abc123)",
  
  "analysis_summary": {
    "overall_rating": "good",
    "efficiency_score": 75,
    "execution_time_ms": 0.3,
    "execution_time_rating": "fast",
    "rows_processed": {"examined": 735, "returned": 735},
    "cost_estimate": 302,
    "primary_concerns": ["SELECT * retrieves all columns...", "Full table scan..."]
  },
  
  "performance_metrics": {
    "execution_metrics": {
      "total_time_ms": 0.3,
      "planning_time_ms": 0.1,
      "rows_examined": 735,
      "rows_returned": 735,
      "cost_estimate": 302
    },
    "database_engine": "postgresql"
  },
  
  "optimization_insights": {
    "available": true,
    "optimization_opportunities": [
      {"priority": "high", "description": "Replace SELECT *...", "type": "query_pattern"},
      {"priority": "high", "description": "Add pagination...", "type": "best_practice"},
      {"priority": "medium", "description": "Run ANALYZE...", "type": "statistics"}
    ]
  },
  
  "recommendations": {
    "available": true,
    "query_rewrites": [...],
    "index_suggestions": [...]
  },
  
  "rewrite_testing": {
    "tested": true,
    "original_performance": {"execution_time_ms": 0.3},
    "rewrite_results": [
      {
        "success": true,
        "sql": "SELECT id, name FROM ...",
        "performance": {"execution_time_ms": 0.2},
        "improvement": {"overall": {"improvement_pct": 33.3}},
        "suggestion_metadata": {"explanation": "..."}
      }
    ]
  },
  
  "readyset_cacheability": {
    "checked": true,
    "cacheable": true,
    "confidence": "high",
    "method": "static_analysis",
    "explanation": "Query is cacheable..."
  },
  
  "metadata": {
    "query": "SELECT * FROM actionable_entity",
    "target": "actionables_prod",
    "analysis_id": "abc123def456",
    "database_engine": "postgresql",
    "llm_info": {"model": "sonnet-4-5", "tokens": 5842, "cost": 0.043}
  }
}
```

---

## Phase 4: Web UI Updates

**Goal**: Update React components to display the full analysis.

### File: `/web-apps/apps/rdst/src/lib/api.ts`

```typescript
// Enhanced types matching the new response structure

export interface AnalysisMetadata {
  query: string;
  target: string;
  analysis_id: string;
  database_engine: string;
  llm_info?: {
    model: string;
    tokens: number;
    cost: number;
  };
}

export interface AnalysisSummary {
  overall_rating: 'excellent' | 'good' | 'fair' | 'poor';
  efficiency_score: number;
  execution_time_ms: number;
  execution_time_rating?: string;
  rows_processed: { examined: number; returned: number };
  cost_estimate: number;
  primary_concerns: string[];
}

export interface RewriteResult {
  success: boolean;
  sql: string;
  performance: { execution_time_ms: number };
  improvement: { overall: { improvement_pct: number } };
  suggestion_metadata: { explanation: string };
}

export interface RewriteTesting {
  tested: boolean;
  skipped_reason?: string;
  original_performance?: { execution_time_ms: number };
  rewrite_results?: RewriteResult[];
}

export interface IndexSuggestion {
  table: string;
  type: string;
  columns: string[];
  sql_statement: string;
  expected_benefit: string;
  rationale: string;
}

export interface ReadysetCacheability {
  checked: boolean;
  cacheable?: boolean;
  confidence?: string;
  method?: string;
  explanation?: string;
}

export interface FormattedAnalysis {
  success: boolean;
  analysis_summary: AnalysisSummary;
  performance_metrics: any;
  optimization_insights: {
    available: boolean;
    optimization_opportunities: Array<{
      priority: string;
      description: string;
      type: string;
    }>;
  };
  recommendations: {
    available: boolean;
    query_rewrites: any[];
    index_suggestions: IndexSuggestion[];
  };
  rewrite_testing?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
  metadata: AnalysisMetadata;
}

export interface AnalysisCompleteEvent {
  success: boolean;
  analysis_id?: string;
  query_hash?: string;
  explain_results: any;
  llm_analysis: any;
  rewrite_testing?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
  formatted: FormattedAnalysis;
}
```

### New Components Needed

1. **PerformanceSummary.tsx** - Rating badge, execution time, concerns
2. **TestedOptimizations.tsx** - Show tested rewrites with % improvement
3. **IndexRecommendations.tsx** - Actionable CREATE INDEX statements
4. **AdditionalRecommendations.tsx** - Other optimization opportunities
5. **ReadysetCacheability.tsx** - Cacheable badge and explanation
6. **AnalysisHeader.tsx** - Target, engine, analysis ID, LLM cost

### Update AnalysisResults.tsx

```tsx
// Use the new formatted structure
export function AnalysisResults({ result }: { result: AnalysisCompleteEvent }) {
  const { formatted } = result;
  
  if (!formatted?.success) {
    return <ErrorPanel message={result.error} />;
  }
  
  return (
    <div className="space-y-6">
      <AnalysisHeader metadata={formatted.metadata} />
      
      <PerformanceSummary summary={formatted.analysis_summary} />
      
      {formatted.rewrite_testing?.tested && (
        <TestedOptimizations testing={formatted.rewrite_testing} />
      )}
      
      {formatted.recommendations?.index_suggestions?.length > 0 && (
        <IndexRecommendations indexes={formatted.recommendations.index_suggestions} />
      )}
      
      {formatted.optimization_insights?.available && (
        <AdditionalRecommendations insights={formatted.optimization_insights} />
      )}
      
      {formatted.readyset_cacheability?.checked && (
        <ReadysetCacheability cacheability={formatted.readyset_cacheability} />
      )}
      
      {/* Existing EXPLAIN plan visualizer */}
      <ExplainPlan plan={result.explain_results} />
    </div>
  );
}
```

---

## Implementation Order

### Sprint 1: Core Service (2-3 days)
1. Create `/rdst/lib/services/` directory
2. Implement `AnalysisService` class
3. Add unit tests for service
4. Verify CLI still works (no changes needed yet)

### Sprint 2: Web API Integration (1-2 days)
1. Update `routes/analyze.py` to use `AnalysisService`
2. Update `models.py` with new fields
3. Test SSE streaming with new events
4. Verify backward compatibility

### Sprint 3: Web UI Components (2-3 days)
1. Create TypeScript types in `api.ts`
2. Build new components:
   - `PerformanceSummary`
   - `TestedOptimizations`
   - `IndexRecommendations`
   - `AdditionalRecommendations`
   - `ReadysetCacheability`
3. Update `AnalysisResults.tsx` to use new structure
4. Style with design tokens

### Sprint 4: Polish & Testing (1-2 days)
1. End-to-end testing CLI vs Web
2. Error handling edge cases
3. Loading states and progress indicators
4. Documentation updates

---

## Migration Notes

### Backward Compatibility
- The new API response includes both raw results AND formatted structure
- Existing web UI code can continue using `explain_results` and `llm_analysis`
- New UI code should prefer `formatted.*` fields

### CLI Migration (Optional)
- CLI can continue using `WorkflowManager` - it works fine
- Or CLI can migrate to `AnalysisService` for consistency
- `output_formatter.py` stays unchanged - it consumes the same dict structure

### Testing Strategy
1. Compare CLI output vs Web UI for same query
2. Verify all sections present in both
3. Check edge cases: failed EXPLAIN, no rewrites, parameterized queries
