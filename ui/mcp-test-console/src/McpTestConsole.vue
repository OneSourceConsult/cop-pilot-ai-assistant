<template>
  <section class="console">
    <header class="header">
      <h1>{{ title }}</h1>
      <nav aria-label="Console views">
        <button :class="{ active: view === 'chat' }" @click="view = 'chat'">Chat</button>
        <button :class="{ active: view === 'activity' }" @click="openActivity">
          Activity <span>{{ events.length }}</span>
        </button>
        <button :class="{ active: view === 'connections' }" @click="openConnections">Connections</button>
      </nav>
    </header>

    <main v-show="view === 'chat'" class="chat">
      <div class="section-title">
        <h2>Chat playground</h2>
        <button v-if="conversationId" class="link" :disabled="loading" @click="resetCurrentConversation">Start over</button>
      </div>

      <div class="messages" aria-live="polite">
        <article v-for="message in messages" :key="message.id" :class="['message', message.role]">
          <div>
            <small>{{ message.role === "user" ? "You" : "Assistant" }}</small>
            <p v-if="message.role === 'user'">{{ message.content }}</p>
            <div v-else class="markdown" v-html="renderMarkdown(message.content)"></div>
          </div>
        </article>
        <article v-if="loading" class="message assistant"><div><small>Assistant</small><p>Working…</p></div></article>
      </div>

      <section v-if="pendingDraft" class="panel">
        <div class="section-title">
          <div><small>Approval required</small><h2>{{ pendingDraft.display_name }}</h2></div>
          <div class="actions">
            <button class="primary" :disabled="loading" @click="confirmDraft(true)">Approve</button>
            <button :disabled="loading" @click="confirmDraft(false)">Cancel</button>
          </div>
        </div>
        <p>{{ pendingDraft.summary }}</p>
        <details><summary>Order details</summary><pre>{{ JSON.stringify(pendingDraft.normalized_arguments, null, 2) }}</pre></details>
      </section>

      <section v-if="lastExecution" class="notice success">
        <strong>Action completed</strong>
        <p>The approved request was sent successfully.</p>
        <details><summary>Technical result</summary><pre>{{ lastExecution.result_preview }}</pre></details>
      </section>

      <section v-if="lastError" class="notice error">
        <strong>Request failed</strong>
        <p>{{ lastError.message }}</p>
        <p v-if="lastErrorDetail" class="error-text">Cause: {{ lastErrorDetail }}</p>
      </section>

      <details v-if="showTraces && lastToolTraces.length" class="panel traces">
        <summary>Developer details · {{ lastToolTraces.length }} tool calls</summary>
        <article v-for="(trace, index) in lastToolTraces" :key="`${trace.tool_name}-${index}`">
          <strong>{{ trace.tool_name }}</strong> · {{ trace.status }}
          <pre>{{ JSON.stringify(trace.arguments, null, 2) }}</pre>
          <pre>{{ trace.result_preview }}</pre>
        </article>
      </details>

      <form class="composer" @submit.prevent="sendMessage">
        <label for="message">Message</label>
        <div>
          <textarea id="message" v-model="draft" rows="2" :disabled="loading" placeholder="What would you like to find or order?" @keydown.enter.exact.prevent="sendMessage" />
          <button class="primary" type="submit" :disabled="loading || !draft.trim()">{{ loading ? "Sending…" : "Send" }}</button>
        </div>
      </form>
    </main>

    <main v-show="view === 'activity'" class="activity">
      <div class="section-title activity-title">
        <div>
          <h2>Activity</h2>
          <span v-if="lastRefreshAt" class="refresh-note" aria-live="polite">Updated {{ formatRefreshTime(lastRefreshAt) }}</span>
        </div>
        <button :disabled="eventsLoading" @click="refreshActivity">{{ eventsLoading ? "Refreshing…" : "Refresh" }}</button>
      </div>

      <div class="metric-groups" aria-label="Activity summary">
        <section class="metric-group">
          <h3>LLM execution</h3>
          <div class="metric-grid two-columns">
            <article><span>Successful</span><strong>{{ eventsError ? "—" : stats.llmSuccessful }}</strong></article>
            <article><span>Failed</span><strong>{{ eventsError ? "—" : stats.llmFailed }}</strong></article>
          </div>
        </section>
        <section class="metric-group">
          <h3>Observability</h3>
          <div class="metric-grid two-columns">
            <article><span>Events forwarded</span><strong>{{ eventsError ? "—" : stats.forwarded }}</strong></article>
            <article><span>Forwarding failed</span><strong>{{ eventsError ? "—" : stats.forwardingFailed }}</strong></article>
          </div>
        </section>
        <section class="metric-group performance-group">
          <h3>Performance</h3>
          <div class="metric-grid three-columns">
            <article><span>Input tokens</span><strong>{{ eventsError ? "—" : stats.inputTokens.toLocaleString() }}</strong></article>
            <article><span>Output tokens</span><strong>{{ eventsError ? "—" : stats.outputTokens.toLocaleString() }}</strong></article>
            <article><span>Avg latency</span><strong>{{ eventsError ? "—" : formatLatency(stats.averageLatency) }}</strong></article>
          </div>
        </section>
      </div>

      <section v-if="viewEnabled && !eventsError" class="activity-controls" aria-label="Activity filters">
        <label class="search-field">
          <span>Search</span>
          <input v-model="searchQuery" type="search" placeholder="Model, provider, tool or ID" />
        </label>
        <label>
          <span>LLM execution</span>
          <select v-model="executionFilter">
            <option value="all">All</option>
            <option value="successful">Successful</option>
            <option value="failed">Failed</option>
          </select>
        </label>
        <label>
          <span>Observability</span>
          <select v-model="forwardingFilter">
            <option value="all">All</option>
            <option value="forwarded">Forwarded</option>
            <option value="failed">Forwarding failed</option>
            <option value="pending">Pending</option>
            <option value="local">Local only</option>
          </select>
        </label>
        <label>
          <span>Approval</span>
          <select v-model="approvalFilter">
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="not-required">Not required</option>
          </select>
        </label>
        <label>
          <span>From</span>
          <input v-model="dateFrom" type="date" />
        </label>
        <label>
          <span>To</span>
          <input v-model="dateTo" type="date" />
        </label>
      </section>

      <div v-if="viewEnabled && !eventsError && events.length" class="results-bar">
        <span>Showing {{ filteredEvents.length }} of {{ events.length }}</span>
        <button v-if="filtersActive" class="link" @click="clearFilters">Clear filters</button>
      </div>

      <section v-if="eventsError" class="empty error-state">
        <strong>Activity could not be loaded</strong>
        <p>{{ eventsError }}</p>
        <button :disabled="eventsLoading" @click="refreshActivity">Try again</button>
      </section>
      <p v-else-if="!viewEnabled" class="empty">Activity history is disabled in this environment.</p>
      <p v-else-if="eventsLoading && !events.length" class="empty" aria-live="polite">Loading activity…</p>
      <p v-else-if="!events.length" class="empty">No LLM activity yet. Send a chat message to create an event.</p>
      <p v-else-if="!filteredEvents.length" class="empty">No activity matches these filters.</p>

      <div v-else class="event-list">
        <details v-for="event in filteredEvents" :key="event.event_id" class="event">
          <summary>
            <div class="event-heading">
              <div class="event-model">
                <strong>{{ event.payload.model }}</strong>
                <span>{{ event.payload.provider }}</span>
              </div>
              <time>{{ formatTime(event.payload.timestamp) }}</time>
            </div>
            <div class="event-statuses" aria-label="Event statuses">
              <span :class="['status', event.payload.success ? 'sent' : 'failed']">LLM: {{ event.payload.success ? "Successful" : "Failed" }}</span>
              <span :class="['status', forwardingTone(event)]">Observability: {{ forwardingStatusLabel(event.delivery_status) }}</span>
              <span :class="['status', event.payload.eventType === 'needs_confirmation' ? 'approval-pending' : 'neutral']">Approval: {{ event.payload.eventType === "needs_confirmation" ? "Pending" : "Not required" }}</span>
            </div>
            <div class="event-performance">
              <span><small>Input</small>{{ event.payload.inputTokens.toLocaleString() }} tokens</span>
              <span><small>Output</small>{{ event.payload.completionTokens.toLocaleString() }} tokens</span>
              <span><small>Latency</small>{{ formatLatency(event.payload.latencyMs) }}</span>
            </div>
          </summary>
          <div class="event-expanded">
            <dl class="event-details">
              <div>
                <dt>Conversation ID</dt>
                <dd class="identifier"><code>{{ event.payload.conversationId }}</code><button class="copy-button" @click="copyIdentifier(event.payload.conversationId, `conversation-${event.event_id}`)">{{ copiedId === `conversation-${event.event_id}` ? "Copied" : "Copy" }}</button></dd>
              </div>
              <div>
                <dt>Event ID</dt>
                <dd class="identifier"><code>{{ event.event_id }}</code><button class="copy-button" @click="copyIdentifier(event.event_id, `event-${event.event_id}`)">{{ copiedId === `event-${event.event_id}` ? "Copied" : "Copy" }}</button></dd>
              </div>
              <div><dt>Recorded</dt><dd>{{ formatTime(event.recorded_at) }}</dd></div>
              <div><dt>Event type</dt><dd><code>{{ event.payload.eventType }}</code></dd></div>
              <div v-if="event.payload.mcpToolSelected"><dt>Tool used</dt><dd><code>{{ event.payload.mcpToolSelected }}</code></dd></div>
              <div><dt>Forwarding</dt><dd>{{ forwardingStatusLabel(event.delivery_status) }}</dd></div>
              <div><dt>HTTP response</dt><dd>{{ event.status_code ?? "No response" }}</dd></div>
            </dl>
            <div v-if="event.payload.reasoningSteps?.length" class="detail-note error-text">
              <strong>Failure details</strong>
              <p>{{ event.payload.reasoningSteps.join(" · ") }}</p>
            </div>
            <div v-if="event.error" class="detail-note error-text">
              <strong>Forwarding error</strong>
              <p>{{ event.error }}</p>
            </div>
          </div>
        </details>
      </div>
    </main>

    <main v-show="view === 'connections'" class="connections">
      <div class="section-title"><h2>Connections</h2></div>
      <div class="connection-grid">
        <section class="panel">
          <div class="connection-head">
            <h3>Product service</h3>
            <span :class="['status', mcpStatus?.status === 'reachable' ? 'sent' : 'failed']">
              {{ mcpStatusLoading ? "Checking" : mcpStatus?.status === "reachable" ? "Ready" : "Unavailable" }}
            </span>
          </div>
          <p>{{ productServiceMessage }}</p>
          <details v-if="mcpStatus">
            <summary>MCP details</summary>
            <dl>
              <div><dt>Server</dt><dd>{{ mcpStatus.tool_server_name }}</dd></div>
              <div><dt>Address</dt><dd>{{ mcpStatus.configured_target }}</dd></div>
              <div><dt>Transport</dt><dd>{{ mcpStatus.tool_server_transport }}</dd></div>
            </dl>
          </details>
          <button :disabled="mcpStatusLoading" @click="refreshMcpStatus">Check connection</button>
        </section>

        <section class="panel">
          <div class="connection-head">
            <h3>Observability endpoint</h3>
            <span :class="['status', deliveryStatus.tone]">{{ deliveryStatus.label }}</span>
          </div>
          <p>{{ deliveryStatus.message }}</p>
          <dl>
            <div><dt>Destination</dt><dd>{{ deliveryTarget ?? "Not configured" }}</dd></div>
            <div><dt>Latest result</dt><dd>{{ latestEvent ? forwardingStatusLabel(latestEvent.delivery_status) : "No attempt yet" }}</dd></div>
          </dl>
          <button :disabled="eventsLoading" @click="refreshEvents">Check connection</button>
        </section>
      </div>
    </main>
  </section>
</template>

<script setup lang="ts">
import { marked } from "marked";
import { computed, onMounted, onUnmounted, ref, toRef } from "vue";

import type { ObservabilityEventRecord } from "./types";
import { useMcpConversation } from "./useMcpConversation";

const props = withDefaults(defineProps<{
  apiBaseUrl?: string;
  getAccessToken?: () => string | null | undefined | Promise<string | null | undefined>;
  title?: string;
  showTraces?: boolean;
}>(), {
  apiBaseUrl: "http://127.0.0.1:8010",
  title: "COP-PILOT LLM Chat",
  showTraces: true,
});

const view = ref<"chat" | "activity" | "connections">("chat");
const apiBaseUrl = toRef(props, "apiBaseUrl");
const {
  conversationId, draft, lastError, lastExecution, lastToolTraces, loading, mcpStatus,
  mcpStatusLoading, messages, observabilityDeliveryEnabled, observabilityDeliveryTarget,
  observabilityEvents: events, observabilityEventsError: eventsError,
  observabilityEventsLoading: eventsLoading, observabilityViewEnabled: viewEnabled,
  pendingDraft, confirmDraft, refreshMcpStatus, refreshObservabilityEvents: refreshEvents,
  resetCurrentConversation, sendMessage,
} = useMcpConversation({
  getAccessToken: () => props.getAccessToken?.(),
  get apiBaseUrl() { return apiBaseUrl.value; },
});

marked.setOptions({ breaks: true, gfm: true });

const searchQuery = ref("");
const executionFilter = ref<"all" | "successful" | "failed">("all");
const forwardingFilter = ref<"all" | "forwarded" | "failed" | "pending" | "local">("all");
const approvalFilter = ref<"all" | "pending" | "not-required">("all");
const dateFrom = ref("");
const dateTo = ref("");
const lastRefreshAt = ref<Date | null>(null);
const copiedId = ref<string | null>(null);

function escapeHtml(value: string) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;").replace(/'/g, "&#39;");
}

function renderMarkdown(content: string) {
  return marked.parse(escapeHtml(content)) as string;
}

const filteredEvents = computed(() => events.value.filter((event) => {
  if (executionFilter.value === "successful" && !event.payload.success) return false;
  if (executionFilter.value === "failed" && event.payload.success) return false;

  if (forwardingFilter.value === "forwarded" && event.delivery_status !== "sent") return false;
  if (forwardingFilter.value === "failed" && !["failed", "dropped"].includes(event.delivery_status)) return false;
  if (forwardingFilter.value === "pending" && event.delivery_status !== "queued") return false;
  if (forwardingFilter.value === "local" && event.delivery_status !== "disabled") return false;

  const approval = event.payload.eventType === "needs_confirmation" ? "pending" : "not-required";
  if (approvalFilter.value !== "all" && approvalFilter.value !== approval) return false;

  const timestamp = new Date(event.payload.timestamp).getTime();
  if (dateFrom.value && timestamp < new Date(`${dateFrom.value}T00:00:00`).getTime()) return false;
  if (dateTo.value && timestamp >= new Date(`${dateTo.value}T00:00:00`).getTime() + 86_400_000) return false;

  const query = searchQuery.value.trim().toLocaleLowerCase();
  if (!query) return true;
  const searchable = [
    event.event_id,
    event.payload.conversationId,
    event.payload.model,
    event.payload.provider,
    event.payload.eventType,
    event.payload.mcpToolSelected,
    event.error,
    ...(event.payload.reasoningSteps ?? []),
  ].filter(Boolean).join(" ").toLocaleLowerCase();
  return searchable.includes(query);
}));

const stats = computed(() => {
  const result = filteredEvents.value.reduce((summary, event) => {
    summary.inputTokens += event.payload.inputTokens;
    summary.outputTokens += event.payload.completionTokens;
    summary.totalLatency += event.payload.latencyMs;
    if (event.payload.success) summary.llmSuccessful += 1;
    else summary.llmFailed += 1;
    if (event.delivery_status === "sent") summary.forwarded += 1;
    if (["failed", "dropped"].includes(event.delivery_status)) summary.forwardingFailed += 1;
    return summary;
  }, {
    llmSuccessful: 0,
    llmFailed: 0,
    forwarded: 0,
    forwardingFailed: 0,
    inputTokens: 0,
    outputTokens: 0,
    totalLatency: 0,
  });
  return {
    ...result,
    averageLatency: filteredEvents.value.length ? Math.round(result.totalLatency / filteredEvents.value.length) : 0,
  };
});

const filtersActive = computed(() => Boolean(
  searchQuery.value
  || executionFilter.value !== "all"
  || forwardingFilter.value !== "all"
  || approvalFilter.value !== "all"
  || dateFrom.value
  || dateTo.value,
));

const lastErrorDetail = computed(() => {
  const detail = lastError.value?.details.provider_message ?? lastError.value?.details.detail_message;
  return typeof detail === "string" && detail.trim() ? detail : null;
});

const latestEvent = computed(() => events.value.find((event) => event.delivery_status !== "disabled"));
const deliveryTarget = computed(() => eventsError.value ? null : observabilityDeliveryTarget.value);
const productServiceMessage = computed(() => mcpStatus.value?.status === "reachable"
  ? `${mcpStatus.value.tool_count ?? 0} tools available.`
  : "The product service could not be reached.");
const deliveryStatus = computed(() => {
  if (eventsError.value) return { tone: "failed", label: "Unavailable", message: "The backend event view could not be reached." };
  if (!observabilityDeliveryEnabled.value) return { tone: "disabled", label: "Not configured", message: "Observability forwarding is off." };
  if (events.value.some((event) => ["failed", "dropped"].includes(event.delivery_status))) return { tone: "failed", label: "Needs attention", message: "A recent forwarding attempt failed." };
  if (latestEvent.value?.delivery_status === "sent") return { tone: "sent", label: "Working", message: "The latest event was acknowledged by the endpoint." };
  return { tone: "queued", label: "Ready", message: "Send a chat message to test forwarding." };
});

function forwardingStatusLabel(status: ObservabilityEventRecord["delivery_status"]) {
  return { sent: "Forwarded", queued: "Pending", failed: "Forwarding failed", dropped: "Not forwarded", disabled: "Local only" }[status];
}

function forwardingTone(event: ObservabilityEventRecord) {
  if (["failed", "dropped"].includes(event.delivery_status)) return "failed";
  return event.delivery_status;
}

function formatTime(timestamp: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(timestamp));
}

function formatRefreshTime(timestamp: Date) {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(timestamp);
}

function formatLatency(latencyMs: number) {
  return latencyMs >= 1000 ? `${(latencyMs / 1000).toFixed(1)} s` : `${latencyMs} ms`;
}

function clearFilters() {
  searchQuery.value = "";
  executionFilter.value = "all";
  forwardingFilter.value = "all";
  approvalFilter.value = "all";
  dateFrom.value = "";
  dateTo.value = "";
}

let copyTimer: number | undefined;
async function copyIdentifier(value: string, key: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
  } else {
    const input = document.createElement("textarea");
    input.value = value;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  copiedId.value = key;
  window.clearTimeout(copyTimer);
  copyTimer = window.setTimeout(() => { copiedId.value = null; }, 1500);
}

async function refreshActivity() {
  await refreshEvents();
  if (!eventsError.value) lastRefreshAt.value = new Date();
}

function openActivity() {
  view.value = "activity";
  void refreshActivity();
}

function openConnections() {
  view.value = "connections";
  void Promise.all([refreshMcpStatus(), refreshEvents()]);
}

let refreshTimer: number | undefined;
onMounted(() => {
  void refreshMcpStatus();
  void refreshActivity();
  refreshTimer = window.setInterval(() => {
    if (view.value !== "chat") void refreshActivity();
  }, 3000);
});
onUnmounted(() => {
  window.clearInterval(refreshTimer);
  window.clearTimeout(copyTimer);
});
</script>

<style scoped src="./McpTestConsole.css"></style>
