# WorkflowManager

AWS Step Functions-style workflow execution engine with LLM integration, database operations, and status tracking.

## Quick Start

```bash
# Run specific workflow
python lib/cli/run_workflow.py --workflow path/to/workflow.json

# Run with status tracking
python lib/cli/run_workflow.py --workflow workflow.json --track

# Test all features
python lib/cli/run_workflow.py --test-all
```

## Workflow Format

```json
{
    "StartAt": "FirstStep",
    "States": {
        "FirstStep": {
            "Type": "Task",
            "Resource": "function_name",
            "Parameters": {"key": "value"},
            "Next": "SecondStep"
        },
        "SecondStep": {
            "Type": "Task",
            "Resource": "call_llm",
            "Parameters": {
                "prompt": "Analyze: {{States.FirstStep.result}}",
                "model": "claude-3-5-sonnet-latest"
            },
            "End": true
        }
    }
}
```

## Built-in Functions

- `get_db_size()` - Database size info
- `get_table_count()` - Table/view counts
- `get_query_stats()` - Query performance
- `analyze_schema()` - Schema analysis
- `call_llm(prompt, model)` - LLM integration

## Template Substitution

Use `{{}}` syntax to inject data between steps:

```json
"prompt": "DB has {{States.GetDBInfo.size_mb}}MB and {{States.GetTableCount.table_count}} tables"
```

## Status Tracking

```python
# Async execution with monitoring
mgr = WorkflowManager.from_file("workflow.json")
workflow_id = mgr.run_async()

# Check status
status = mgr.get_workflow_status(workflow_id)
print(f"Status: {status.status.value}")
print(f"Current Step: {status.current_step}")

# Get step details
for step_name, step_result in status.steps.items():
    print(f"{step_name}: {step_result.status.value}")
```

## Adding Custom Managers

### DataManager Integration

```python
# lib/workflows/data_manager.py
from lib.data_manager.data_manager import DataManager

def execute_data_command(command_set_name: str, command_name: str, **kwargs):
    data_mgr = DataManager(connection_config=kwargs.get('connection_configs'))
    result = data_mgr.execute_command(command_set_name, command_name)
    return {"success": result.get('success'), "data": result.get('data')}

DATA_MANAGER_FUNCTIONS = {"execute_data_command": execute_data_command}
```

### CacheManager Integration

```python
# lib/workflows/cache_manager.py
from lib.cache_manager.cache_manager import CacheManager

def apply_cache_changes(**kwargs):
    cache_mgr = CacheManager(config_manager=ConfigurationManager())
    cache_mgr.apply_cache_changes()
    return {"success": True, "message": "Cache changes applied"}

CACHE_MANAGER_FUNCTIONS = {"apply_cache_changes": apply_cache_changes}
```

### Combined Registry

```python
# In run_workflow.py
registry = {
    **DEFAULT_FUNCTIONS,        # Built-in DB + LLM functions
    **DATA_MANAGER_FUNCTIONS,   # DataManager integration
    **CACHE_MANAGER_FUNCTIONS,  # CacheManager integration
}
mgr = WorkflowManager.from_file(workflow_path, resources=registry)
```

## Real-World Example

```json
{
    "Comment": "ReadySet optimization workflow",
    "StartAt": "GetCachedQueries",
    "States": {
        "GetCachedQueries": {
            "Type": "Task",
            "Resource": "get_cached_query_ids",
            "Next": "GetDBStats"
        },
        "GetDBStats": {
            "Type": "Task",
            "Resource": "get_db_size",
            "Next": "AnalyzeWithLLM"
        },
        "AnalyzeWithLLM": {
            "Type": "Task",
            "Resource": "call_llm",
            "Parameters": {
                "prompt": "ReadySet has {{States.GetCachedQueries.count}} cached queries and {{States.GetDBStats.size_mb}}MB data. Optimize?",
                "model": "claude-3-5-sonnet-latest"
            },
            "Next": "ApplyCacheChanges"
        },
        "ApplyCacheChanges": {
            "Type": "Task",
            "Resource": "apply_cache_changes",
            "End": true
        }
    }
}
```