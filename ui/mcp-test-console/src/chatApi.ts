import type { ChatResponse, McpStatusResponse, ObservabilityEventsResponse } from "./types";

export type AccessTokenProvider = () => string | null | undefined | Promise<string | null | undefined>;

async function request<T>(baseUrl: string, path: string, init?: RequestInit, getAccessToken?: AccessTokenProvider): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = (await getAccessToken?.())?.trim();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function sendChatMessage(
  baseUrl: string,
  message: string,
  threadId: string | null,
  getAccessToken?: AccessTokenProvider,
): Promise<ChatResponse> {
  return request<ChatResponse>(baseUrl, "/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  }, getAccessToken);
}

export async function confirmProductOrder(
  baseUrl: string,
  threadId: string,
  draftId: string,
  fingerprint: string,
  idempotencyKey: string,
  getAccessToken?: AccessTokenProvider,
): Promise<ChatResponse> {
  return request<ChatResponse>(baseUrl, `/v1/chat/${threadId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      draft_id: draftId,
      fingerprint,
      idempotency_key: idempotencyKey,
    }),
  }, getAccessToken);
}

export async function cancelProductOrder(baseUrl: string, threadId: string, getAccessToken?: AccessTokenProvider): Promise<ChatResponse> {
  return request<ChatResponse>(baseUrl, `/v1/chat/${threadId}/cancel`, {
    method: "POST",
  }, getAccessToken);
}

export async function resetConversation(baseUrl: string, threadId: string, getAccessToken?: AccessTokenProvider): Promise<void> {
  await request(baseUrl, `/v1/chat/${threadId}/reset`, { method: "POST" }, getAccessToken);
}

export async function fetchMcpStatus(baseUrl: string, getAccessToken?: AccessTokenProvider): Promise<McpStatusResponse> {
  return request<McpStatusResponse>(baseUrl, "/v1/runtime/mcp-status", undefined, getAccessToken);
}

export async function fetchObservabilityEvents(
  baseUrl: string,
  getAccessToken?: AccessTokenProvider,
): Promise<ObservabilityEventsResponse> {
  return request<ObservabilityEventsResponse>(
    baseUrl,
    "/v1/runtime/observability/events",
    undefined,
    getAccessToken,
  );
}
