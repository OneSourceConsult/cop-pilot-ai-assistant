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
  pending_action: "confirm_product_order" | null;
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
  message: string;
  tool_count: number | null;
  tool_names: string[];
  error: ErrorResponse | null;
};
