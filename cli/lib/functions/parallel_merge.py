from typing import Dict, Any


def merge_parallel_analysis_results(
    analysis_branch: Dict[str, Any] = None,
    readyset_branch: Dict[str, Any] = None,
    query: str = None,
    target: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Merge results from parallel analysis and ReadySet testing branches.

    Args:
        analysis_branch: Results from regular analysis (Branch A)
        readyset_branch: Results from ReadySet setup and testing (Branch B)
        query: Original SQL query
        target: Target database name
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing merged results
    """
    try:
        if not analysis_branch:
            return {
                "success": False,
                "error": f"Missing analysis branch results (type: {type(analysis_branch)}, value: {analysis_branch})"
            }

        if not readyset_branch:
            return {
                "success": False,
                "error": f"Missing ReadySet branch results (type: {type(readyset_branch)}, value: {readyset_branch})"
            }

        # Extract analysis results from Branch A
        registry_normalization = analysis_branch.get('registry_normalization', {})
        llm_parameterization = analysis_branch.get('llm_parameterization', {})
        explain_results = analysis_branch.get('explain_results', {})
        query_metrics = analysis_branch.get('query_metrics', {})
        schema_collection = analysis_branch.get('schema_collection', {})
        llm_analysis = analysis_branch.get('llm_analysis', {})
        rewrite_test_results = analysis_branch.get('rewrite_test_results', {})
        readyset_cacheability_static = analysis_branch.get('readyset_cacheability', {})

        # Extract ReadySet results from Branch B
        readyset_explain_cache = readyset_branch.get('readyset_explain_cache', {})
        readyset_container = readyset_branch.get('readyset_container', {})
        readyset_ready = readyset_branch.get('readyset_ready', {})

        # Merge ReadySet cacheability results
        # Prefer real ReadySet EXPLAIN CREATE CACHE results over static analysis
        readyset_final = {
            "static_analysis": readyset_cacheability_static,
            "readyset_explain_cache": readyset_explain_cache,
            "readyset_container": {
                "status": readyset_container.get('success', False),
                "container_name": readyset_container.get('container_name'),
                "port": readyset_container.get('port')
            },
            "readyset_ready": readyset_ready.get('ready', False)
        }

        # Determine final cacheability verdict
        # ReadySet EXPLAIN CREATE CACHE takes precedence over static analysis
        if readyset_explain_cache.get('success'):
            final_cacheable = readyset_explain_cache.get('cacheable', False)
            final_confidence = readyset_explain_cache.get('confidence', 'unknown')
            final_method = 'readyset_container'
        else:
            # Fall back to static analysis
            final_cacheable = readyset_cacheability_static.get('cacheable', False)
            final_confidence = readyset_cacheability_static.get('confidence', 'unknown')
            final_method = 'static_analysis'

        readyset_final['final_verdict'] = {
            "cacheable": final_cacheable,
            "confidence": final_confidence,
            "method": final_method
        }

        # Combine all results
        merged = {
            "success": True,
            "query": query,
            "target": target,
            "registry_normalization": registry_normalization,
            "llm_parameterization": llm_parameterization,
            "explain_results": explain_results,
            "query_metrics": query_metrics,
            "schema_collection": schema_collection,
            "llm_analysis": llm_analysis,
            "rewrite_test_results": rewrite_test_results,
            "readyset_cacheability": readyset_final,
            "readyset_explain_cache": readyset_explain_cache
        }

        # Debug output
        print(f"\n=== MERGE DEBUG ===")
        print(f"analysis_branch type: {type(analysis_branch)}")
        print(f"readyset_branch type: {type(readyset_branch)}")
        print(f"explain_results type: {type(explain_results)}, value: {explain_results}")
        print(f"llm_analysis type: {type(llm_analysis)}, keys: {llm_analysis.keys() if isinstance(llm_analysis, dict) else 'N/A'}")
        print(f"merged keys: {list(merged.keys())}")
        print(f"===================\n")

        return merged

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to merge parallel results: {str(e)}"
        }
