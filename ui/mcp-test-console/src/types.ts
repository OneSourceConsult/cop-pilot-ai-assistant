export type ToolTrace = {
  tool_name: string;
  arguments: Record<string, unknown>;
  status: string;
  stage: string;
  result_preview: string;
};

export type ProductOrderDraft = {
  draft_id: string;
  tool_name: string;
  display_name: string;
  normalized_arguments: Record<string, unknown>;
  summary: string;
  fingerprint: string;
  requires_confirmation: boolean;
};

export type ExecutionResult = {
  execution_token: string;
  tool_name: string;
  status: string;
  result_preview: string;
};

export type ErrorResponse = {
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
};

export type ChatResponse = {
  thread_id: string;
  status: "ready" | "needs_confirmation" | "blocked" | "executed" | "error";
  message: string;
  tool_traces: ToolTrace[];
  draft: ProductOrderDraft | null;
  execution_result: ExecutionResult | null;
  error: ErrorResponse | null;
};


export type McpStatusResponse = {
  status: "reachable" | "unavailable";
  tool_server_name: string;
  tool_server_transport: string;
  configured_target: string;
  configured_command: string | null;
  configured_args: string[];
  checked_at: string;
  message: string;
  tool_count: number | null;
  tool_names: string[];
  error: ErrorResponse | null;
};

export type AgentEventPayload = {
  timestamp: string;
  agentId: string;
  conversationId: string;
  eventType: string;
  model: string;
  provider: string;
  inputTokens: number;
  completionTokens: number;
  maxTokens: number;
  latencyMs: number;
  success: boolean;
  inputPrompt: string;
  responseText: string;
  reasoningSteps?: string[];
  mcpToolSelected?: string;
  toolSelectionReasoning?: string;
  toolSelectionConfidence?: Record<string, number>;
};

export type ObservabilityEventRecord = {
  event_id: string;
  recorded_at: string;
  delivery_status: "queued" | "sent" | "failed" | "dropped" | "disabled";
  status_code?: number;
  error?: string;
  payload: AgentEventPayload;
};

export type ObservabilityEventsResponse = {
  delivery_enabled: boolean;
  delivery_target?: string;
  view_enabled: boolean;
  events: ObservabilityEventRecord[];
};
