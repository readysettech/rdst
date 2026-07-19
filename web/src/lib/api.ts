import type { components as apiComponents } from './api.generated';
import { api as typedClient } from './client';

async function throwIfNotOk(response: Response, ctx: string): Promise<void> {
  if (response.ok) return;
  const body = await response.text().catch(() => '');
  throw new Error(body || `${ctx}: ${response.status}`);
}

export type AnalyzeRequest = apiComponents['schemas']['AnalyzeRequest'];

export type ProgressEvent = apiComponents['schemas']['ProgressEvent'];

export type ExplainResults = apiComponents['schemas']['ExplainResults'];
export type PerformanceAssessment = apiComponents['schemas']['PerformanceAssessment'];
export type RewriteSuggestion = apiComponents['schemas']['RewriteSuggestion'];
export type IndexRecommendation = apiComponents['schemas']['IndexRecommendation'];
export type OptimizationOpportunity = apiComponents['schemas']['OptimizationOpportunity'];
export type LLMAnalysis = apiComponents['schemas']['LLMAnalysis'];
export type TestedRewrite = apiComponents['schemas']['TestedRewrite'];
export type RewriteTesting = apiComponents['schemas']['RewriteTesting'];
export type ReadysetCacheability = apiComponents['schemas']['ReadysetCacheability'];

export type AnalysisSummary = apiComponents['schemas']['AnalysisSummary'];

export type InitTargetInfo = apiComponents['schemas']['InitTargetInfo'];
export type InitStatusResponse = apiComponents['schemas']['InitStatusResponse'];

export type AnalysisMetadata = apiComponents['schemas']['AnalysisMetadata'];
export type FormattedAnalysis = apiComponents['schemas']['FormattedAnalysis'];
export type CompleteEvent = apiComponents['schemas']['CompleteEvent'];

export async function fetchInitStatus(): Promise<InitStatusResponse> {
  const response = await fetch('/api/init/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch init status: ${response.status}`);
  }
  return response.json();
}

export type ErrorEvent = apiComponents['schemas']['ErrorEvent'];

export type AnalysisState = 'idle' | 'analyzing' | 'complete' | 'error';

export type TargetInfo = apiComponents['schemas']['TargetInfo'];

export type StatusResponse = apiComponents['schemas']['StatusResponse'];

export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch('/api/status');
  if (!response.ok) {
    throw new Error(`Failed to fetch status: ${response.status}`);
  }
  return response.json();
}

export type TargetSummary = apiComponents['schemas']['TargetSummaryResponse'];
export type TargetListResponse = apiComponents['schemas']['TargetListResponse'];

/**
 * List configured targets with their connection details (host/port). Used by
 * the benchmark rail to flag non-local/remote destinations (B5).
 */
export async function fetchTargets(): Promise<TargetSummary[]> {
  const { data, response } = await typedClient.GET('/api/configure/targets');
  await throwIfNotOk(response, 'Failed to list targets');
  if (data && 'targets' in data) return data.targets;
  return [];
}

export type EnvRequirement = apiComponents['schemas']['EnvRequirement'];
export type EnvRequirementKind = EnvRequirement['kind'];
export type EnvRequirementSource = EnvRequirement['source'];
export type EnvRequirementsResponse = apiComponents['schemas']['EnvRequirementsResponse'];

export type SetEnvSecretRequest = apiComponents['schemas']['EnvSetRequest'];
export type SetEnvSecretResponse = apiComponents['schemas']['EnvSetResponse'];

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

export type SchemaResponse = apiComponents['schemas']['SchemaResponse'];

export async function fetchSchema(target?: string): Promise<SchemaResponse> {
  const { data, response } = await typedClient.GET('/api/schema', {
    params: { query: { target: target ?? null } },
  });
  await throwIfNotOk(response, 'Failed to fetch schema');
  if (!data) throw new Error('Missing response body');
  return data;
}

export type QueryRegistryEntry = apiComponents['schemas']['QueryRegistryEntry'];
export type QueryRegistryResponse = apiComponents['schemas']['QueryRegistryResponse'];

export async function fetchQueryRegistry(limit?: number, offset = 0): Promise<QueryRegistryResponse> {
  const { data, response } = await typedClient.GET('/api/query-registry', {
    params: { query: { limit: limit ?? null, offset } },
  });
  await throwIfNotOk(response, 'Failed to fetch registry');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function addQueryToRegistry(sql: string, target?: string): Promise<{ success: boolean; hash?: string | null; error?: string | null }> {
  const { data, response } = await typedClient.POST('/api/query-registry', {
    body: { sql, target },
  });
  await throwIfNotOk(response, 'Failed to add query');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function removeQueryFromRegistry(hash: string): Promise<{ success: boolean; error?: string | null }> {
  const { data, response } = await typedClient.DELETE('/api/query-registry/{query_hash}', {
    params: { path: { query_hash: hash } },
  });
  await throwIfNotOk(response, 'Failed to remove query');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function updateQueryTag(hash: string, tag: string): Promise<{ success: boolean; error?: string | null }> {
  const { data, response } = await typedClient.PATCH('/api/query-registry/{query_hash}/tag', {
    params: { path: { query_hash: hash } },
    body: { tag },
  });
  await throwIfNotOk(response, 'Failed to update tag');
  if (!data) throw new Error('Missing response body');
  return data;
}

export type UpdateSqlResponse = apiComponents['schemas']['UpdateSqlResponse'];

export async function updateQuerySql(hash: string, sql: string): Promise<UpdateSqlResponse> {
  const { data, response } = await typedClient.PATCH('/api/query-registry/{query_hash}/sql', {
    params: { path: { query_hash: hash } },
    body: { sql },
  });
  await throwIfNotOk(response, 'Failed to update SQL');
  if (!data) throw new Error('Missing response body');
  return data;
}

export type ImportQueriesResponse = apiComponents['schemas']['ImportQueriesResponse'];

export async function importQueries(
  file: string,
  options?: { update?: boolean; target?: string },
): Promise<ImportQueriesResponse> {
  const { data, response } = await typedClient.POST('/api/query-registry/import', {
    body: { file, update: options?.update, target: options?.target },
  });
  await throwIfNotOk(response, 'Failed to import queries');
  if (!data) throw new Error('Missing response body');
  return data;
}

export type ReadysetContainerStatus = apiComponents['schemas']['ContainerStatus'];
export type ReadysetSetupRequest = apiComponents['schemas']['SetupRequest'];
export type ReadysetCacheRequest = apiComponents['schemas']['CacheRequest'];
export type ReadysetSetupResult = apiComponents['schemas']['ReadysetSetupCompleteEvent'];
export type ReadysetExplainResult = apiComponents['schemas']['ReadysetExplainCompleteEvent'];
export type ReadysetCreateCacheResult = apiComponents['schemas']['ReadysetCacheCompleteEvent'];

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

export type ConversationStatus = apiComponents['schemas']['ConversationStatusResponse'];

export type Message = apiComponents['schemas']['MessageResponse'];

export type InteractiveMessageRequest = apiComponents['schemas']['InteractiveMessageRequest'];

// Report/Feedback Types

export type ReportRequest = apiComponents['schemas']['ReportRequest'];
export type ReportSentiment = NonNullable<ReportRequest['sentiment']>;
export type ReportResponse = apiComponents['schemas']['ReportResponse'];

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

export type BenchmarkRequest = apiComponents['schemas']['BenchmarkRequest'];
export type BenchmarkQueryInput = apiComponents['schemas']['BenchmarkQueryInput'];
export type BenchmarkMode = BenchmarkRequest['mode'];

// QueryBenchmarkStats comes from the generated schema; re-exported for consumers.
export type QueryBenchmarkStats = apiComponents['schemas']['QueryBenchmarkStats'];

export type BenchmarkState = 'idle' | 'running' | 'complete' | 'error';

// Trial Types (re-exports from generated)

export type TrialRegisterResponse = apiComponents['schemas']['TrialRegisterResponse'];
export type TrialActivateResponse = apiComponents['schemas']['TrialActivateResponse'];
export type TrialStatusResponse = apiComponents['schemas']['TrialStatusResponse'];
export type TrialSimulationResponse = apiComponents['schemas']['TrialSimulationResponse'];

export async function registerTrial(email: string): Promise<TrialRegisterResponse> {
  const { data, response } = await typedClient.POST('/api/trial/register', {
    body: { email },
  });
  await throwIfNotOk(response, 'Failed to register trial');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function activateTrial(token: string, email: string, emailTier?: string): Promise<TrialActivateResponse> {
  const { data, response } = await typedClient.POST('/api/trial/activate', {
    body: { token, email, email_tier: emailTier ?? null },
  });
  await throwIfNotOk(response, 'Failed to activate trial');
  if (!data) throw new Error('Missing response body');
  return data;
}

export async function fetchTrialStatus(): Promise<TrialStatusResponse> {
  const { data, response } = await typedClient.GET('/api/trial/status', {});
  await throwIfNotOk(response, 'Failed to fetch trial status');
  if (!data) throw new Error('Missing response body');
  return data;
}

// Browse (directory picker)

export type BrowseDirectoryEntry = apiComponents['schemas']['DirectoryEntry'];
export type BrowseResponse = apiComponents['schemas']['BrowseResponse'];

export async function fetchBrowse(path?: string, ext?: string): Promise<BrowseResponse> {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  if (ext) params.set('ext', ext);
  const query = params.toString();
  const response = await fetch(query ? `/api/browse?${query}` : '/api/browse');
  if (!response.ok) {
    throw new Error(`Failed to browse directory: ${response.status}`);
  }
  return response.json();
}

export async function simulateTrialExhausted(): Promise<TrialSimulationResponse> {
  const { data, response } = await typedClient.POST('/api/trial/simulate/exhaust', {});
  await throwIfNotOk(response, 'Failed to simulate trial exhaustion');
  if (!data) throw new Error('Missing response body');
  return data;
}

export type ClearKeyringResponse = apiComponents['schemas']['ClearKeyringResponse'];

type ClearKeyringErrorPayload = {
  message?: string | null;
  errors?: string[];
  detail?: string | { msg?: string }[];
};

function formatClearKeyringErrorMessage(
  payload: ClearKeyringErrorPayload | null | undefined,
  fallback: string,
 ): string {
  if (!payload) return fallback;

  const parts: string[] = [];

  if (typeof payload.message === 'string' && payload.message.trim()) {
    parts.push(payload.message.trim());
  }

  const errors = Array.isArray(payload.errors)
    ? payload.errors.map((error) => error.trim()).filter(Boolean)
    : [];
  if (errors.length > 0) {
    parts.push(errors.join(' '));
  }

  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    parts.push(payload.detail.trim());
  }

  if (Array.isArray(payload.detail)) {
    const detailMessages = payload.detail
      .map((detail) => (typeof detail?.msg === 'string' ? detail.msg.trim() : ''))
      .filter(Boolean);
    if (detailMessages.length > 0) {
      parts.push(detailMessages.join(' '));
    }
  }

  return parts.join(' ').trim() || fallback;
}

export async function clearKeyring(): Promise<ClearKeyringResponse> {
  const response = await fetch('/api/dev/clear-keyring', { method: 'POST' });

  let payload: ClearKeyringErrorPayload | ClearKeyringResponse | null = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    throw new Error(
      formatClearKeyringErrorMessage(
        payload,
        `Failed to clear keyring: ${response.status}`
      )
    );
  }

  const result = payload as ClearKeyringResponse | null;
  if (!result?.success) {
    throw new Error(
      formatClearKeyringErrorMessage(result, 'Failed to clear keyring.')
    );
  }

  return result;
}
