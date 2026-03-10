export interface AnalyzeRequest {
  query: string;
  target?: string;
  fast?: boolean;
  skip_rewrites?: boolean;
  skip_readyset?: boolean;
  skip_storage?: boolean;
  model?: string;
}

export interface ProgressEvent {
  stage: string;
  percent: number;
  message?: string;
}

export interface ExplainResults {
  success: boolean;
  database_engine: string;
  execution_time_ms: number;
  rows_examined: number;
  rows_returned: number;
  cost_estimate: number;
  explain_plan?: unknown;
}

export interface PerformanceAssessment {
  overall_rating: 'excellent' | 'good' | 'fair' | 'poor';
  efficiency_score: number;
  execution_time_rating?: string;
  primary_concerns: string[];
}

export interface RewriteSuggestion {
  rewritten_sql: string;
  explanation: string;
  expected_improvement: string;
  priority: 'high' | 'medium' | 'low';
  optimization_type: string;
}

export interface IndexRecommendation {
  sql: string;
  table: string;
  columns: string[];
  index_type: string;
  rationale: string;
  estimated_impact: 'high' | 'medium' | 'low';
  caveats?: string[];
}

export interface OptimizationOpportunity {
  priority: 'high' | 'medium' | 'low';
  description: string;
  type: string;
}

export interface LLMAnalysis {
  success?: boolean;
  performance_assessment?: PerformanceAssessment;
  execution_analysis?: {
    bottlenecks: string[];
    scan_efficiency: string;
  };
  rewrite_suggestions?: RewriteSuggestion[];
  index_recommendations?: IndexRecommendation[];
  optimization_opportunities?: OptimizationOpportunity[];
  llm_model?: string;
  token_usage?: {
    input: number;
    output: number;
    total: number;
    estimated_cost_usd: number;
  };
}

export interface TestedRewrite {
  success: boolean;
  sql: string;
  performance: {
    execution_time_ms: number;
  };
  improvement: {
    overall: {
      improvement_pct: number;
    };
  };
  suggestion_metadata: {
    explanation: string;
  };
}

export interface RewriteTesting {
  tested: boolean;
  skipped_reason?: string;
  message?: string;
  original_performance?: {
    execution_time_ms: number;
  };
  rewrite_results?: TestedRewrite[];
  best_rewrite?: TestedRewrite;
}

export interface ReadysetCacheability {
  checked: boolean;
  cacheable?: boolean;
  confidence?: 'high' | 'medium' | 'low' | 'unknown';
  method?: string;
  explanation?: string;
  issues?: string[];
  warnings?: string[];
}

export interface AnalysisSummary {
  overall_rating: string;
  efficiency_score: number;
  execution_time_ms: number;
  execution_time_rating?: string;
  rows_processed: {
    examined: number;
    returned: number;
  };
  cost_estimate: number;
  primary_concerns: string[];
  explain_analyze_skipped?: boolean;
}

export interface InitStatusResponse {
  initialized: boolean;
  targets: Array<{
    name: string;
    has_password: boolean;
    is_default: boolean;
    engine?: string;
  }>;
  default_target: string | null;
  llm_configured: boolean;
}

export interface AnalysisMetadata {
  query: string;
  normalized_query?: string;
  parameterized_sql?: string;
  target: string;
  analysis_id: string;
  database_engine: string;
  analyzed_at?: string;
  llm_info?: {
    model: string;
    tokens: number;
    cost: number;
  };
}

export interface FormattedAnalysis {
  success: boolean;
  message?: string;
  analysis_summary: AnalysisSummary;
  performance_metrics: {
    execution_metrics: {
      total_time_ms: number;
      planning_time_ms?: number;
      actual_time_ms?: number;
      rows_examined: number;
      rows_returned: number;
      cost_estimate: number;
    };
    database_engine: string;
    explain_available: boolean;
  };
  optimization_insights: {
    available: boolean;
    error?: string;
    explanation?: string;
    optimization_opportunities: OptimizationOpportunity[];
  };
  recommendations: {
    available: boolean;
    query_rewrites: Array<{
      id?: string;
      type: string;
      priority: string;
      confidence?: string;
      sql: string;
      explanation: string;
      expected_improvement: string;
      trade_offs?: string;
    }>;
    index_suggestions: Array<{
      id?: string;
      table: string;
      type: string;
      columns: string[];
      sql_statement: string;
      expected_benefit: string;
      rationale: string;
      storage_impact?: string;
    }>;
  };
  rewrite_testing?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
  metadata: AnalysisMetadata;
}

export interface CompleteEvent {
  success: boolean;
  analysis_id?: string;
  query_hash?: string;
  explain_results: ExplainResults;
  llm_analysis: LLMAnalysis;
  rewrite_testing?: RewriteTesting;
  readyset_cacheability?: ReadysetCacheability;
  formatted?: FormattedAnalysis;
}

export async function fetchInitStatus(): Promise<InitStatusResponse> {
  const response = await fetch('/api/init/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch init status: ${response.status}`);
  }
  return response.json();
}

export interface ErrorEvent {
  message: string;
  stage?: string;
  partial_results?: {
    explain_results?: ExplainResults;
    llm_analysis?: LLMAnalysis;
  };
}

export type AnalysisState = 'idle' | 'analyzing' | 'complete' | 'error';

export interface TargetInfo {
  name: string;
  has_password: boolean;
  is_default: boolean;
}

export interface StatusResponse {
  configured: boolean;
  default_target: string | null;
  targets: TargetInfo[];
  version: string | null;
  error: string | null;
}

export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch('/api/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.status}`);
  }
  return response.json();
}

export type EnvRequirementKind = 'target_password' | 'anthropic_api_key';
export type EnvRequirementSource = 'config' | 'process_env' | 'secure_store' | 'trial' | 'trial_exhausted' | 'missing';

export interface EnvRequirement {
  kind: EnvRequirementKind;
  accepted_names: string[];
  target: string | null;
  satisfied: boolean;
  source: EnvRequirementSource;
}

export interface EnvRequirementsResponse {
  keyring_available: boolean;
  requirements: EnvRequirement[];
}

export interface SetEnvSecretRequest {
  name: string;
  value: string;
  persist?: boolean;
}

export interface SetEnvSecretResponse {
  success: boolean;
  name: string;
  persisted: boolean;
  session_only: boolean;
  message?: string;
}

export async function fetchEnvRequirements(): Promise<EnvRequirementsResponse> {
  const response = await fetch('/api/env/requirements');
  if (!response.ok) {
    throw new Error(`Failed to fetch env requirements: ${response.status}`);
  }
  return response.json();
}

export async function setEnvSecret(payload: SetEnvSecretRequest): Promise<SetEnvSecretResponse> {
  const response = await fetch('/api/env/set', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: payload.name,
      value: payload.value,
      persist: payload.persist ?? true,
    }),
  });
  if (!response.ok) {
    throw new Error(`Failed to set secret: ${response.status}`);
  }
  return response.json();
}

export interface SchemaResponse {
  tables: Record<string, string[]>;
  dialect: 'postgresql' | 'mysql';
  error?: string | null;
}

export async function fetchSchema(target?: string): Promise<SchemaResponse> {
  const url = target ? `/api/schema?target=${encodeURIComponent(target)}` : '/api/schema';
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch schema: ${response.status}`);
  }
  return response.json();
}

export interface QueryRegistryEntry {
  sql: string;
  hash: string;
  tag: string;
  last_analyzed: string;
  target: string;
  frequency: number;
  source: string;
  most_recent_params?: Record<string, string | number>;
}

export interface QueryRegistryResponse {
  queries: QueryRegistryEntry[];
  error?: string | null;
}

export async function fetchQueryRegistry(limit = 50): Promise<QueryRegistryResponse> {
  const response = await fetch(`/api/query-registry?limit=${limit}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch registry: ${response.status}`);
  }
  return response.json();
}

export async function addQueryToRegistry(sql: string, target?: string): Promise<{ success: boolean; hash?: string; error?: string }> {
  const response = await fetch('/api/query-registry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql, target }),
  });
  if (!response.ok) {
    throw new Error(`Failed to add query: ${response.status}`);
  }
  return response.json();
}

export async function removeQueryFromRegistry(hash: string): Promise<{ success: boolean; error?: string }> {
  const response = await fetch(`/api/query-registry/${hash}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(`Failed to remove query: ${response.status}`);
  }
  return response.json();
}

export async function updateQueryTag(hash: string, tag: string): Promise<{ success: boolean; error?: string }> {
  const response = await fetch(`/api/query-registry/${hash}/tag`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag }),
  });
  if (!response.ok) {
    throw new Error(`Failed to update tag: ${response.status}`);
  }
  return response.json();
}

export interface ReadysetContainerStatus {
  running: boolean;
  test_db_running: boolean;
  readyset_running: boolean;
  readyset_port: number | null;
  test_db_port: number | null;
  target: string | null;
}

export interface ReadysetSetupRequest {
  target?: string;
}

export interface ReadysetCacheRequest {
  query: string;
  target?: string;
}

export interface ReadysetSetupResult {
  success: boolean;
  readyset_port?: number;
  test_db_port?: number;
  already_running?: boolean;
}

export interface ReadysetExplainResult {
  success: boolean;
  cacheable: boolean;
  confidence: 'high' | 'medium' | 'low' | 'unknown';
  explanation: string;
  issues: string[];
  readyset_port?: number;
}

export interface ReadysetCreateCacheResult {
  success: boolean;
  cached: boolean;
  cache_id: string | null;
  message: string;
  error?: string;
  readyset_port?: number;
}

export type ReadysetOperationState = 'idle' | 'running' | 'complete' | 'error';

export async function fetchReadysetStatus(target?: string): Promise<ReadysetContainerStatus> {
  const url = target 
    ? `/api/readyset/status?target=${encodeURIComponent(target)}` 
    : '/api/readyset/status';
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch readyset status: ${response.status}`);
  }
  return response.json();
}

// Interactive Mode Types

export type InteractiveState = 'idle' | 'sending' | 'receiving' | 'error';

export interface ConversationStatus {
  exists: boolean;
  message_count?: number;
  started_at?: string;
}

export interface Message {
  role: string;
  content: string;
  timestamp: string;
}

export interface InteractiveMessageRequest {
  message: string;
  continue_existing?: boolean;
  analysis_results?: any;
}

// Report/Feedback Types

export type ReportSentiment = 'positive' | 'negative' | 'neutral';

export interface ReportRequest {
  reason: string;
  sentiment: ReportSentiment;
  query_hash?: string;
  email?: string;
  include_query?: boolean;
  include_plan?: boolean;
}

export interface ReportResponse {
  success: boolean;
  error?: string;
}

export async function submitReport(request: ReportRequest): Promise<ReportResponse> {
  const response = await fetch('/api/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(`Failed to submit report: ${response.status}`);
  }
  return response.json();
}

// Benchmark Types

export type BenchmarkMode = 'interval' | 'concurrency';

export interface BenchmarkQueryInput {
  identifier?: string;
  sql?: string;
}

export interface BenchmarkRequest {
  queries: (string | BenchmarkQueryInput)[];
  target?: string;
  mode: BenchmarkMode;
  interval_ms?: number;
  concurrency?: number;
  duration_seconds?: number;
  max_count?: number;
}

export interface QueryBenchmarkStats {
  query_name: string;
  query_hash: string;
  executions: number;
  successes: number;
  failures: number;
  min_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
  last_error?: string;
}

export interface BenchmarkProgress {
  type: 'progress' | 'complete' | 'error';
  elapsed_seconds: number;
  total_executions: number;
  total_successes: number;
  total_failures: number;
  qps: number;
  queries: QueryBenchmarkStats[];
  error?: string;
}

export type BenchmarkState = 'idle' | 'running' | 'complete' | 'error';

// Trial Types

export interface TrialRegisterResponse {
  success: boolean;
  limit_display?: string;
  email_tier?: string;
  error_code?: string;
  detail?: string;
  did_you_mean?: string;
  status_code: number;
}

export interface TrialActivateResponse {
  success: boolean;
  message?: string;
}

export interface TrialStatusResponse {
  active: boolean;
  email?: string;
  status?: string;
  remaining_cents?: number;
  limit_cents?: number;
  remaining_tokens_display?: string;
  limit_tokens_display?: string;
  percent_remaining?: number;
}

export interface TrialSimulationResponse {
  success: boolean;
  message?: string;
}

export async function registerTrial(email: string): Promise<TrialRegisterResponse> {
  const response = await fetch('/api/trial/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    throw new Error(`Failed to register trial: ${response.status}`);
  }
  return response.json();
}

export async function activateTrial(token: string, email: string, emailTier?: string): Promise<TrialActivateResponse> {
  const response = await fetch('/api/trial/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, email, email_tier: emailTier }),
  });
  if (!response.ok) {
    throw new Error(`Failed to activate trial: ${response.status}`);
  }
  return response.json();
}

export async function fetchTrialStatus(): Promise<TrialStatusResponse> {
  const response = await fetch('/api/trial/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch trial status: ${response.status}`);
  }
  return response.json();
}

export async function simulateTrialExhausted(): Promise<TrialSimulationResponse> {
  const response = await fetch('/api/trial/simulate/exhaust', {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to simulate trial exhaustion: ${response.status}`);
  }
  return response.json();
}
