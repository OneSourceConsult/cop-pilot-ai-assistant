import type { ChatResponse, McpStatusResponse } from "./types";

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function sendChatMessage(
  baseUrl: string,
  message: string,
  threadId: string | null,
): Promise<ChatResponse> {
  return request<ChatResponse>(baseUrl, "/v1/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  });
}

export async function confirmProductOrder(
  baseUrl: string,
  threadId: string,
  confirmed: boolean,
): Promise<ChatResponse> {
  return request<ChatResponse>(baseUrl, `/v1/chat/${threadId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed }),
  });
}

export async function resetConversation(baseUrl: string, threadId: string): Promise<void> {
  await request(baseUrl, `/v1/chat/${threadId}/reset`, { method: "POST" });
}

export async function fetchMcpStatus(baseUrl: string): Promise<McpStatusResponse> {
  return request<McpStatusResponse>(baseUrl, "/v1/runtime/mcp-status");
}
