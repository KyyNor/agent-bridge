import { QueryClient, type QueryClientConfig } from '@tanstack/vue-query'

/**
 * 服务端状态的统一默认策略。
 *
 * 页面仍可覆盖这些策略：运行中的任务使用条件轮询，低频配置数据使用更长
 * 的 staleTime。默认关闭焦点自动刷新，避免控制台页面在用户筛选时突然换页。
 */
export const queryClientConfig: QueryClientConfig = {
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 300_000,
      retry: false,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
}

/** 应用与非组件 composable 共用的单例缓存客户端。 */
export const queryClient = new QueryClient(queryClientConfig)

/**
 * 所有服务端查询键从这里生成，确保筛选条件和资源身份始终参与缓存隔离。
 * 不把 Vue ref 放入 key；调用方需要先取它的当前值。
 */
export const queryKeys = {
  toolCallLogs: (params: Record<string, string | number | boolean>) =>
    ['tool-call-logs', 'list', params] as const,
  toolCallLog: (logId: string) => ['tool-call-logs', 'detail', logId] as const,
  toolCallStats: (dimensions: string) => ['tool-call-stats', { dimensions }] as const,
  agentRuns: (params: Record<string, string | number | boolean>) =>
    ['agent-runs', 'list', params] as const,
  agentRun: (runKey: string) => ['agent-runs', 'detail', runKey] as const,
  agentRunEvents: (runKey: string) => ['agent-runs', 'events', runKey] as const,
  agentRunPayload: (runKey: string, ref: string) => ['agent-runs', 'payload', runKey, ref] as const,
  agentRunSubagent: (runKey: string, taskId: string) =>
    ['agent-runs', 'subagent', runKey, taskId] as const,
  workflowArtifacts: (params: Record<string, string | number | boolean | undefined>) =>
    ['workflow-artifacts', 'list', params] as const,
  workflowArtifact: (artifactId: string, profileKey?: string) =>
    ['workflow-artifacts', 'detail', artifactId, profileKey] as const,
  workflowArtifactHistory: (params: Record<string, string | number | boolean | undefined>) =>
    ['workflow-artifacts', 'history', params] as const,
  workflowRun: (runId: string) => ['workflow-runs', 'detail', runId] as const,
  workflowRunEvents: (runId: string) => ['workflow-runs', 'events', runId] as const,
  workflowRunLogs: (runId: string) => ['workflow-runs', 'logs', runId] as const,
  knowledgeBases: () => ['knowledge-bases'] as const,
  knowledgeBackends: () => ['knowledge-backends'] as const,
}
