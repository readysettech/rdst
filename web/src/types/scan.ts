import type {
  ExplainResults,
  LLMAnalysis,
  RewriteTesting,
  ReadysetCacheability,
  FormattedAnalysis,
} from '../lib/api';

/**
 * Types for Scan feature
 */

export type ScanState = 'idle' | 'scanning' | 'complete' | 'error';
export type ScanPhase =
  | 'config'
  | 'discovery'
  | 'extraction'
  | 'conversion'
  | 'registry'
  | 'analysis';

export interface ScanFile {
  file: string;
  orms: string[];
  lines: number;
}

export interface ScanQuery {
  file: string;
  function: string;
  class: string;
  orm_code: string;
  snippet_hash: string;
  terminal_method: string;
  start_line: number;
  end_line: number;
  orm_type: string;
  sql: string;
  status: 'sql' | 'skipped' | 'pending';
  skip_reason?: string;
  issues: string[];
  hash?: string;
  imports_builder?: boolean;
}

export interface ScanSummary {
  files_count: number;
  queries_total: number;
  queries_sql: number;
  queries_skipped: number;
  cache_hits: number;
  cache_misses: number;
  registry_new: number;
  registry_updated: number;
  registry_total: number;
  registry_skipped: boolean;
  message?: string;
  analysis?: ScanAnalysisSummary;
}

export interface ScanAnalysisSummary {
  mode: 'deep' | 'shallow';
  total_analyzed: number;
  successful: number;
  failed: number;
  worst_score: number | string;
  ci_status: 'pass' | 'warn' | 'fail';
  ci_exit_code: number;
  warn_threshold: number;
  fail_threshold: number;
  by_query: ScanAnalyzedQuery[];
  performance_issues: Array<{ hash: string; file: string; issue: string }>;
  recommendations: Array<{ hash: string; recommendation: string }>;
  failed_queries: Array<{ hash: string; function: string; sql: string; error: string }>;
}

export interface ScanNestedAnalysisData {
  performance_assessment?: LLMAnalysis['performance_assessment'];
  index_recommendations?: LLMAnalysis['index_recommendations'];
  optimization_opportunities?: LLMAnalysis['optimization_opportunities'];
  rewrite_suggestions?: LLMAnalysis['rewrite_suggestions'];
  rewrite_testing?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
}

export interface ScanLLMAnalysis extends LLMAnalysis {
  analysis_results?: ScanNestedAnalysisData;
}

export interface ScanRawAnalysis {
  analysis_id?: string;
  query_hash?: string;
  explain_results?: ExplainResults;
  llm_analysis?: ScanLLMAnalysis;
  rewrite_testing?: RewriteTesting;
  rewrite_test_results?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
  formatted?: FormattedAnalysis;
}

export interface ScanAnalyzedQuery {
  hash: string;
  file: string;
  function: string;
  line: number;
  sql: string;
  risk_score: number | null;
  rating: string;
  issues: string[];
  recommendations: string[];
  execution_time_ms?: number;
  rewrite_benchmarks?: string[];
  /** Full analysis JSON from `rdst analyze --json` — available in deep mode */
  raw_analysis?: ScanRawAnalysis;
}

// SSE event payload types are generated from the backend OpenAPI schema;
// consume them via `components['schemas']['ScanEvent']` in `lib/api.generated`.
// The rich payload types above (ScanFile, ScanQuery, ScanSummary) refine the
// generated `{[key: string]: unknown}` fields at the consumer site.