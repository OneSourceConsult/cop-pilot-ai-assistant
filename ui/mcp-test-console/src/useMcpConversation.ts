import { ref } from "vue";
import type { AccessTokenProvider } from "./chatApi";

import {
  cancelProductOrder,
  confirmProductOrder,
  fetchMcpStatus,
  fetchObservabilityEvents,
  resetConversation,
  sendChatMessage,
} from "./chatApi";
import type {
  ChatResponse,
  ErrorResponse,
  ExecutionResult,
  McpStatusResponse,
  ObservabilityEventRecord,
  ProductOrderDraft,
  ToolTrace,
} from "./types";

export type Message = { id: string; role: "user" | "assistant"; content: string };

type ConversationOptions = {
  apiBaseUrl: string;
  getAccessToken?: AccessTokenProvider;
};

const INITIAL_MESSAGE =
  "I’m ready. What would you like to find or order?";

function createMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function createIdempotencyKey(): string {
  return createMessageId();
}

export function useMcpConversation(options: ConversationOptions) {
  const draft = ref("");
  const loading = ref(false);
  const conversationId = ref<string | null>(null);
  const lastToolTraces = ref<ToolTrace[]>([]);
  const mcpStatus = ref<McpStatusResponse | null>(null);
  const mcpStatusLoading = ref(false);
  const observabilityDeliveryEnabled = ref(false);
  const observabilityDeliveryTarget = ref<string | null>(null);
  const observabilityEvents = ref<ObservabilityEventRecord[]>([]);
  const observabilityEventsError = ref<string | null>(null);
  const observabilityEventsLoading = ref(false);
  const observabilityViewEnabled = ref(false);
  const pendingDraft = ref<ProductOrderDraft | null>(null);
  const lastExecution = ref<ExecutionResult | null>(null);
  const lastError = ref<ErrorResponse | null>(null);
  const messages = ref<Message[]>([
    {
      id: createMessageId(),
      role: "assistant",
      content: INITIAL_MESSAGE,
    },
  ]);

  function appendAssistantResponse(data: ChatResponse) {
    // The API omits tool_traces unless diagnostics were requested.
    lastToolTraces.value = data.tool_traces ?? [];
    pendingDraft.value = data.draft;
    lastExecution.value = data.execution_result;
    lastError.value = data.error;
    messages.value.push({
      id: createMessageId(),
      role: "assistant",
      content: data.message || "I didn’t receive a response. Please try again.",
    });
  }

  async function refreshMcpStatus() {
    if (mcpStatusLoading.value) {
      return;
    }

    mcpStatusLoading.value = true;
    try {
      mcpStatus.value = await fetchMcpStatus(options.apiBaseUrl, options.getAccessToken);
    } catch (error) {
      mcpStatus.value = {
        status: "unavailable",
        tool_server_name: "unknown",
        tool_server_transport: "unknown",
        configured_target: "unknown",
        configured_command: null,
        configured_args: [],
        checked_at: new Date().toISOString(),
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

  async function refreshObservabilityEvents() {
    if (observabilityEventsLoading.value) {
      return;
    }

    observabilityEventsLoading.value = true;
    try {
      const data = await fetchObservabilityEvents(options.apiBaseUrl, options.getAccessToken);
      observabilityEventsError.value = null;
      observabilityDeliveryEnabled.value = data.delivery_enabled;
      observabilityDeliveryTarget.value = data.delivery_target ?? null;
      observabilityViewEnabled.value = data.view_enabled;
      observabilityEvents.value = data.events;
    } catch (error) {
      observabilityEventsError.value = error instanceof Error ? error.message : String(error);
    } finally {
      observabilityEventsLoading.value = false;
    }
  }

  async function sendMessage() {
    const content = draft.value.trim();
    if (!content || loading.value) {
      return;
    }

    messages.value.push({ id: createMessageId(), role: "user", content });
    draft.value = "";
    loading.value = true;

    try {
      const data = await sendChatMessage(options.apiBaseUrl, content, conversationId.value, options.getAccessToken);
      conversationId.value = data.thread_id;
      appendAssistantResponse(data);
      await refreshMcpStatus();
      void refreshObservabilityEvents();
    } catch (error) {
      messages.value.push({
        id: createMessageId(),
        role: "assistant",
        content: "I couldn’t complete that request because the service is unavailable. Check the connection and try again.",
      });
      lastError.value = null;
      await refreshMcpStatus();
      void refreshObservabilityEvents();
    } finally {
      loading.value = false;
    }
  }

  async function confirmDraft(confirmed: boolean) {
    if (!conversationId.value || loading.value) {
      return;
    }
    if (confirmed && !pendingDraft.value) {
      return;
    }

    messages.value.push({
      id: createMessageId(),
      role: "user",
      content: confirmed ? "Confirm this product order." : "Cancel this product order draft.",
    });

    loading.value = true;
    try {
      const data = confirmed
        ? await confirmProductOrder(
            options.apiBaseUrl,
            conversationId.value,
            pendingDraft.value!.draft_id,
            pendingDraft.value!.fingerprint,
            createIdempotencyKey(),
            options.getAccessToken,
          )
        : await cancelProductOrder(options.apiBaseUrl, conversationId.value, options.getAccessToken);
      appendAssistantResponse(data);
      await refreshMcpStatus();
      void refreshObservabilityEvents();
    } catch (error) {
      messages.value.push({
        id: createMessageId(),
        role: "assistant",
        content: "I couldn’t complete the approval. Check the connection and try again.",
      });
      lastError.value = null;
      await refreshMcpStatus();
      void refreshObservabilityEvents();
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
      await resetConversation(options.apiBaseUrl, conversationId.value, options.getAccessToken);
      conversationId.value = null;
      lastToolTraces.value = [];
      pendingDraft.value = null;
      lastExecution.value = null;
      lastError.value = null;
      messages.value = [
        {
          id: createMessageId(),
          role: "assistant",
          content: INITIAL_MESSAGE,
        },
      ];
      await refreshMcpStatus();
      void refreshObservabilityEvents();
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
    observabilityDeliveryEnabled,
    observabilityDeliveryTarget,
    observabilityEvents,
    observabilityEventsError,
    observabilityEventsLoading,
    observabilityViewEnabled,
    pendingDraft,
    confirmDraft,
    refreshMcpStatus,
    refreshObservabilityEvents,
    resetCurrentConversation,
    sendMessage,
  };
}
