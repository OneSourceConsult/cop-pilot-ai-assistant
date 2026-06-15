import { ref } from "vue";

import { confirmProductOrder, fetchMcpStatus, resetConversation, sendChatMessage } from "./chatApi";
import type { ChatResponse, ErrorResponse, ExecutionResult, McpStatusResponse, ProductOrderDraft, ToolTrace } from "./types";

export type Message = { id: string; role: "user" | "assistant"; content: string };

type ConversationOptions = {
  apiBaseUrl: string;
};

const INITIAL_MESSAGE =
  "Ready. Ask about products or request a product order through the connected MCP server.";

export function useMcpConversation(options: ConversationOptions) {
  const draft = ref("");
  const loading = ref(false);
  const conversationId = ref<string | null>(null);
  const lastToolTraces = ref<ToolTrace[]>([]);
  const mcpStatus = ref<McpStatusResponse | null>(null);
  const mcpStatusLoading = ref(false);
  const pendingDraft = ref<ProductOrderDraft | null>(null);
  const lastExecution = ref<ExecutionResult | null>(null);
  const lastError = ref<ErrorResponse | null>(null);
  const messages = ref<Message[]>([
    {
      id: crypto.randomUUID(),
      role: "assistant",
      content: INITIAL_MESSAGE,
    },
  ]);

  function appendAssistantResponse(data: ChatResponse) {
    lastToolTraces.value = data.tool_traces;
    pendingDraft.value = data.draft;
    lastExecution.value = data.execution_result;
    lastError.value = data.error;
    messages.value.push({
      id: crypto.randomUUID(),
      role: "assistant",
      content: data.message || "No response returned.",
    });
  }

  async function refreshMcpStatus() {
    if (mcpStatusLoading.value) {
      return;
    }

    mcpStatusLoading.value = true;
    try {
      mcpStatus.value = await fetchMcpStatus(options.apiBaseUrl);
    } catch (error) {
      mcpStatus.value = {
        status: "unavailable",
        tool_server_name: "unknown",
        tool_server_transport: "unknown",
        configured_target: "unknown",
        configured_command: null,
        configured_args: [],
        message: error instanceof Error ? error.message : String(error),
        tool_count: null,
        tool_names: [],
        error: {
          code: "status_request_failed",
          message: error instanceof Error ? error.message : String(error),
          retryable: true,
          details: {},
        },
      };
    } finally {
      mcpStatusLoading.value = false;
    }
  }

  async function sendMessage() {
    const content = draft.value.trim();
    if (!content || loading.value) {
      return;
    }

    messages.value.push({ id: crypto.randomUUID(), role: "user", content });
    draft.value = "";
    loading.value = true;

    try {
      const data = await sendChatMessage(options.apiBaseUrl, content, conversationId.value);
      conversationId.value = data.thread_id;
      appendAssistantResponse(data);
      await refreshMcpStatus();
    } catch (error) {
      messages.value.push({
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Request failed: ${error instanceof Error ? error.message : String(error)}`,
      });
      lastError.value = null;
      await refreshMcpStatus();
    } finally {
      loading.value = false;
    }
  }

  async function confirmDraft(confirmed: boolean) {
    if (!conversationId.value || loading.value) {
      return;
    }

    messages.value.push({
      id: crypto.randomUUID(),
      role: "user",
      content: confirmed ? "Confirm this product order." : "Cancel this product order draft.",
    });

    loading.value = true;
    try {
      const data = await confirmProductOrder(options.apiBaseUrl, conversationId.value, confirmed);
      appendAssistantResponse(data);
      await refreshMcpStatus();
    } catch (error) {
      messages.value.push({
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Confirmation failed: ${error instanceof Error ? error.message : String(error)}`,
      });
      lastError.value = null;
      await refreshMcpStatus();
    } finally {
      loading.value = false;
    }
  }

  async function resetCurrentConversation() {
    if (!conversationId.value || loading.value) {
      return;
    }

    loading.value = true;
    try {
      await resetConversation(options.apiBaseUrl, conversationId.value);
      conversationId.value = null;
      lastToolTraces.value = [];
      pendingDraft.value = null;
      lastExecution.value = null;
      lastError.value = null;
      messages.value = [
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Conversation reset. Ready for a new product-order test.",
        },
      ];
      await refreshMcpStatus();
    } finally {
      loading.value = false;
    }
  }

  return {
    conversationId,
    draft,
    lastError,
    lastExecution,
    lastToolTraces,
    loading,
    mcpStatus,
    mcpStatusLoading,
    messages,
    pendingDraft,
    confirmDraft,
    refreshMcpStatus,
    resetCurrentConversation,
    sendMessage,
  };
}
