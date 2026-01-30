# Refactor CLI Commands to Use AnalysisService

## Context: What Was Done

The `rdst analyze` CLI command was refactored from using `WorkflowManager` (JSON workflow orchestration) to using `AnalysisService` (Python class orchestration).

**Before:**
```
CLI → WorkflowManager → analyze_workflow_simple.json → /lib/functions/*
```

**After:**
```
CLI → AnalysisService → /lib/functions/*
```

## Files Changed

1. **`/rdst/lib/cli/analyze_command.py`**
   - `_run_analyze_workflow()` - Replaced WorkflowManager instantiation with AnalysisService
   - `_run_workflow_with_progress()` - Replaced with `_run_analysis_with_progress()` using AnalysisStage callbacks

## Key Refactoring Pattern

### Step 1: Replace WorkflowManager with AnalysisService

```python
# OLD
from ..workflow_manager.workflow_manager import WorkflowManager, DEFAULT_FUNCTIONS
from ..functions import ANALYZE_WORKFLOW_FUNCTIONS

workflow_path = Path(__file__).parent.parent / "workflows" / "analyze_workflow_simple.json"
mgr = WorkflowManager.from_file(str(workflow_path), resources={**DEFAULT_FUNCTIONS, **ANALYZE_WORKFLOW_FUNCTIONS})
result = mgr.run(initial_input)

# NEW
from ..services import AnalysisService, AnalysisRequest, AnalysisStage

service = AnalysisService()
request = AnalysisRequest(
    query=resolved_input.sql,
    target=target,
    target_config=target_config,
    fast_mode=fast,
    skip_rewrite_testing=False,
    skip_readyset_check=True,
    skip_storage=False,
    llm_model=None,
)
result = service.run_analysis_sync(request, on_progress=on_progress)
```

### Step 2: Map Progress Callbacks

```python
# OLD: Monkey-patched WorkflowManager step callbacks
step_names = {
    "ValidateQuerySafety": "Validating query safety",
    "PerformLLMAnalysis": "Analysis via model...",
    # ... workflow state names
}

# NEW: AnalysisStage enum callbacks
stage_names = {
    "validating": "Validating query safety",
    "analyzing_llm": "Analysis via model...",
    # ... stage IDs from AnalysisStage enum
}

def on_progress(stage: AnalysisStage, detail: str = None):
    friendly_name = stage_names.get(stage.stage_id, stage.message)
    # Update CLI spinner display
```

### Step 3: Map Result Structure

```python
# AnalysisResult dataclass fields → workflow context dict for format_analyze_output()
workflow_context = {
    "query": resolved_input.sql,
    "normalized_query": resolved_input.normalized_sql,
    "target": target,
    "target_config": target_config,
    "explain_results": result.explain_results,
    "llm_analysis": result.llm_analysis,
    "rewrite_test_results": result.rewrite_testing,
    "readyset_cacheability": result.readyset_cacheability,
    "storage_result": {"analysis_id": result.analysis_id},
    "FormatFinalResults": result.formatted,  # Key field for output_formatter.py
}
```

## AnalysisService Interface Reference

**Location:** `/rdst/lib/services/analysis_service.py`

```python
@dataclass
class AnalysisRequest:
    query: str
    target: str
    target_config: Dict[str, Any]
    fast_mode: bool = False
    skip_rewrite_testing: bool = False
    skip_readyset_check: bool = False
    skip_storage: bool = False
    llm_model: Optional[str] = None
    parameter_values: Dict[str, Any] = field(default_factory=dict)

@dataclass  
class AnalysisResult:
    success: bool
    explain_results: Dict[str, Any]
    llm_analysis: Dict[str, Any]
    rewrite_testing: Dict[str, Any]
    readyset_cacheability: Dict[str, Any]
    analysis_id: Optional[str]
    query_hash: Optional[str]
    target: str
    database_engine: str
    formatted: Dict[str, Any]
    error: Optional[str]
    failed_stage: Optional[str]

class AnalysisStage(Enum):
    VALIDATING = ("validating", 5, "Validating query safety...")
    NORMALIZING = ("normalizing", 10, "Normalizing query...")
    EXECUTING_EXPLAIN = ("executing_explain", 20, "Running EXPLAIN ANALYZE...")
    COLLECTING_SCHEMA = ("collecting_schema", 35, "Collecting schema context...")
    ANALYZING_LLM = ("analyzing_llm", 50, "Analyzing with AI...")
    TESTING_REWRITES = ("testing_rewrites", 70, "Testing query rewrites...")
    CHECKING_READYSET = ("checking_readyset", 85, "Checking Readyset cacheability...")
    STORING_RESULTS = ("storing_results", 95, "Storing results...")
    COMPLETE = ("complete", 100, "Analysis complete")
```

## Task for Next Agent

**Objective:** Refactor `[COMMAND_NAME]` CLI command to use shared service pattern.

**Steps:**
1. Identify if command uses WorkflowManager or direct function calls
2. If WorkflowManager: Create/extend appropriate Service class in `/rdst/lib/services/`
3. Replace workflow orchestration with Service class
4. Map progress callbacks to CLI spinner display
5. Map result structure to maintain compatibility with existing formatters
6. Verify imports work: `python -c "from lib.cli.[command] import [Class]"`
7. Test command runs successfully

**Do NOT:**
- Change the underlying `/lib/functions/*` - those stay the same
- Change output formatting - maintain compatibility with existing formatters
- Remove progress/spinner display - users expect visual feedback

**Verify:**
- `source .venv/bin/activate && python -c "from lib.cli.[module] import [Class]"` succeeds
- Command runs without import errors
- Progress spinner displays during execution
- Output format unchanged

## Note

Most other commands (`top`, `query`, `schema`, `configure`) may not use WorkflowManager at all - check first before attempting refactor. The pattern above is specifically for commands that were using JSON workflow orchestration.
