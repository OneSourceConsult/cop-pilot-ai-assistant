<template>
  <section class="mcp-console">
    <header class="mcp-console__header">
      <div>
        <h2>{{ title }}</h2>
        <p>Thin chat UI for testing product discovery and guarded product-order execution.</p>
      </div>
      <div class="mcp-console__header-actions">
        <section class="mcp-console__status-card">
          <div class="mcp-console__trace-head">
            <strong>MCP status</strong>
            <div class="mcp-console__trace-meta">
              <span :class="`mcp-console__trace-status mcp-console__trace-status--${mcpStatus?.status ?? 'unknown'}`">
                {{ mcpStatusLoading ? 'checking' : mcpStatus?.status ?? 'unknown' }}
              </span>
            </div>
          </div>
          <p>{{ mcpStatus?.message ?? 'Status not loaded yet.' }}</p>
          <p v-if="mcpStatus"><strong>Server:</strong> {{ mcpStatus.tool_server_name }} ({{ mcpStatus.tool_server_transport }})</p>
          <p v-if="mcpStatus"><strong>Target:</strong> {{ mcpStatus.configured_target }}</p>
          <p v-if="mcpStatus && mcpStatus.configured_command"><strong>Command:</strong> {{ mcpStatus.configured_command }}</p>
          <pre v-if="mcpStatus && mcpStatus.configured_args.length > 0">{{ JSON.stringify(mcpStatus.configured_args, null, 2) }}</pre>
          <p v-if="mcpStatus && mcpStatus.tool_count !== null"><strong>Tools:</strong> {{ mcpStatus.tool_count }}</p>
          <pre v-if="mcpStatus && mcpStatus.error">{{ JSON.stringify(mcpStatus.error.details, null, 2) }}</pre>
          <div class="mcp-console__actions">
            <button class="mcp-console__ghost" :disabled="loading || mcpStatusLoading" @click="handleRefreshStatus">
              Refresh MCP status
            </button>
            <button class="mcp-console__ghost" :disabled="loading || !conversationId" @click="handleReset">
              Reset
            </button>
          </div>
        </section>
      </div>
    </header>

    <div class="mcp-console__messages">
      <article
        v-for="message in messages"
        :key="message.id"
        class="mcp-console__message"
        :class="`mcp-console__message--${message.role}`"
      >
        <div class="mcp-console__bubble">
          <span class="mcp-console__role">{{ message.role === "user" ? "You" : "Assistant" }}</span>
          <p v-if="message.role === 'user'">{{ message.content }}</p>
          <div v-else class="mcp-console__markdown" v-html="renderMarkdown(message.content)"></div>
        </div>
      </article>

      <article v-if="loading" class="mcp-console__message mcp-console__message--assistant">
        <div class="mcp-console__bubble">
          <span class="mcp-console__role">Assistant</span>
          <p>Working...</p>
        </div>
      </article>
    </div>

    <section v-if="pendingDraft" class="mcp-console__draft">
      <div class="mcp-console__draft-head">
        <div>
          <span class="mcp-console__pill">Pending confirmation</span>
          <h3>{{ pendingDraft.display_name }}</h3>
          <p>{{ pendingDraft.summary }}</p>
        </div>
        <div class="mcp-console__actions">
          <button class="mcp-console__send" :disabled="loading || !conversationId" @click="handleConfirm(true)">
            Confirm order
          </button>
          <button class="mcp-console__ghost" :disabled="loading || !conversationId" @click="handleConfirm(false)">
            Cancel draft
          </button>
        </div>
      </div>

      <pre>{{ JSON.stringify(pendingDraft.normalized_arguments, null, 2) }}</pre>
    </section>

    <section v-if="lastExecution" class="mcp-console__execution">
      <div class="mcp-console__trace-head">
        <strong>Latest execution</strong>
        <span class="mcp-console__trace-status mcp-console__trace-status--executed">
          {{ lastExecution.status }}
        </span>
      </div>
      <p><strong>Tool:</strong> {{ lastExecution.tool_name }}</p>
      <p><strong>Execution token:</strong> {{ lastExecution.execution_token }}</p>
      <pre>{{ lastExecution.result_preview }}</pre>
    </section>

    <section v-if="lastError" class="mcp-console__error">
      <div class="mcp-console__trace-head">
        <strong>Latest error</strong>
        <span class="mcp-console__trace-status mcp-console__trace-status--error">{{ lastError.code }}</span>
      </div>
      <p>{{ lastError.message }}</p>
      <p><strong>Retryable:</strong> {{ lastError.retryable ? "yes" : "no" }}</p>
      <pre v-if="Object.keys(lastError.details).length > 0">{{ JSON.stringify(lastError.details, null, 2) }}</pre>
    </section>

    <details v-if="showTraces && lastToolTraces.length > 0" class="mcp-console__traces" open>
      <summary>Latest tool traces</summary>
      <div v-for="(trace, index) in lastToolTraces" :key="`${trace.tool_name}-${index}`" class="mcp-console__trace">
        <div class="mcp-console__trace-head">
          <strong>{{ trace.tool_name }}</strong>
          <div class="mcp-console__trace-meta">
            <span class="mcp-console__trace-stage">{{ trace.stage }}</span>
            <span :class="`mcp-console__trace-status mcp-console__trace-status--${trace.status}`">
              {{ trace.status }}
            </span>
          </div>
        </div>
        <pre>{{ JSON.stringify(trace.arguments, null, 2) }}</pre>
        <pre>{{ trace.result_preview }}</pre>
      </div>
    </details>

    <form class="mcp-console__composer" @submit.prevent="handleSend">
      <textarea
        v-model="draft"
        class="mcp-console__input"
        rows="3"
        :disabled="loading"
        placeholder="Ask to inspect product offerings or prepare a product order..."
        @keydown.enter.exact.prevent="handleSend"
      />
      <button class="mcp-console__send" :disabled="loading || !draft.trim()" type="submit">Send</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { marked } from "marked";
import { onMounted, toRef } from "vue";

import { useMcpConversation } from "./useMcpConversation";

const props = withDefaults(
  defineProps<{
    apiBaseUrl?: string;
    title?: string;
    showTraces?: boolean;
  }>(),
  {
    apiBaseUrl: "http://127.0.0.1:8010",
    title: "LLM Layer Console",
    showTraces: true,
  },
);

const apiBaseUrl = toRef(props, "apiBaseUrl");
marked.setOptions({
  breaks: true,
  gfm: true,
});

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMarkdown(content: string): string {
  return marked.parse(escapeHtml(content)) as string;
}

const {
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
} = useMcpConversation({
  get apiBaseUrl() {
    return apiBaseUrl.value;
  },
});

async function handleSend() {
  await sendMessage();
}

async function handleConfirm(confirmed: boolean) {
  await confirmDraft(confirmed);
}

async function handleReset() {
  await resetCurrentConversation();
}

async function handleRefreshStatus() {
  await refreshMcpStatus();
}

onMounted(() => {
  void refreshMcpStatus();
});
</script>

<style scoped>
.mcp-console {
  display: grid;
  grid-template-rows: auto 1fr auto auto auto;
  gap: 1rem;
  min-height: 70vh;
  min-width: 0;
  padding: 1.25rem;
  border: 1px solid #d8e2ec;
  border-radius: 20px;
  background:
    radial-gradient(circle at top right, rgba(91, 141, 239, 0.08), transparent 28%),
    linear-gradient(180deg, #fbfdff 0%, #f3f7fb 100%);
  color: #16324f;
}

.mcp-console__header,
.mcp-console__draft-head,
.mcp-console__trace-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.mcp-console__header h2,
.mcp-console__draft h3 {
  margin: 0;
}

.mcp-console__header p,
.mcp-console__draft p {
  margin: 0.35rem 0 0;
  color: #59738f;
  overflow-wrap: anywhere;
}

.mcp-console__header-actions {
  display: flex;
  flex: 1 1 20rem;
  justify-content: flex-end;
  min-width: 0;
}

.mcp-console__status-card {
  width: min(100%, 24rem);
  min-width: 0;
  padding: 1rem;
  border: 1px solid #d8e2ec;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 30px rgba(22, 50, 79, 0.06);
}

.mcp-console__status-card p {
  margin: 0.45rem 0 0;
  color: #3f5974;
  overflow-wrap: anywhere;
}

.mcp-console__messages {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-height: 18rem;
  overflow: auto;
}

.mcp-console__message {
  display: flex;
  min-width: 0;
}

.mcp-console__message--user {
  justify-content: flex-end;
}

.mcp-console__bubble,
.mcp-console__draft,
.mcp-console__error,
.mcp-console__execution,
.mcp-console__traces {
  border: 1px solid #d8e2ec;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 30px rgba(22, 50, 79, 0.06);
}

.mcp-console__bubble {
  max-width: min(42rem, 90%);
  min-width: 0;
  padding: 0.9rem 1rem;
}

.mcp-console__message--user .mcp-console__bubble {
  background: #16324f;
  color: white;
  border-color: #16324f;
}

.mcp-console__role {
  display: inline-block;
  margin-bottom: 0.35rem;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.7;
}

.mcp-console__bubble p {
  margin: 0;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.mcp-console__markdown {
  line-height: 1.6;
}

.mcp-console__markdown :deep(p),
.mcp-console__markdown :deep(ul),
.mcp-console__markdown :deep(ol) {
  margin: 0;
}

.mcp-console__markdown :deep(p + p),
.mcp-console__markdown :deep(p + ul),
.mcp-console__markdown :deep(p + ol),
.mcp-console__markdown :deep(ul + p),
.mcp-console__markdown :deep(ol + p),
.mcp-console__markdown :deep(ul + ol),
.mcp-console__markdown :deep(ol + ul) {
  margin-top: 0.75rem;
}

.mcp-console__markdown :deep(ul),
.mcp-console__markdown :deep(ol) {
  padding-left: 1.35rem;
}

.mcp-console__markdown :deep(li + li) {
  margin-top: 0.35rem;
}

.mcp-console__markdown :deep(strong) {
  font-weight: 700;
}

.mcp-console__markdown :deep(code) {
  padding: 0.12rem 0.35rem;
  border-radius: 6px;
  background: rgba(22, 50, 79, 0.08);
  font-size: 0.92em;
}

.mcp-console__markdown :deep(pre) {
  margin-top: 0.75rem;
}

.mcp-console__draft,
.mcp-console__error,
.mcp-console__execution,
.mcp-console__traces {
  padding: 1rem;
}

.mcp-console__pill,
.mcp-console__trace-stage {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  background: #e8f0ff;
  color: #244977;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.mcp-console__actions,
.mcp-console__trace-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.mcp-console__traces summary {
  cursor: pointer;
  font-weight: 600;
}

.mcp-console__trace {
  margin-top: 0.85rem;
  padding-top: 0.85rem;
  border-top: 1px solid #e6edf5;
}

.mcp-console__trace-status {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
}

.mcp-console__trace-status--allow,
.mcp-console__trace-status--success,
.mcp-console__trace-status--executed {
  background: #e7f8ef;
  color: #0f6b3d;
}

.mcp-console__trace-status--clarify,
.mcp-console__trace-status--blocked,
.mcp-console__trace-status--duplicate {
  background: #fff4d8;
  color: #8b5e00;
}

.mcp-console__trace-status--deny,
.mcp-console__trace-status--error,
.mcp-console__trace-status--unauthorized {
  background: #ffe6e3;
  color: #a12b20;
}

.mcp-console__composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.75rem;
}

.mcp-console__input {
  resize: vertical;
  min-height: 4.25rem;
  padding: 0.9rem 1rem;
  border-radius: 16px;
  border: 1px solid #c9d7e5;
  background: white;
  color: inherit;
  font: inherit;
}

.mcp-console__send,
.mcp-console__ghost {
  min-width: 8rem;
  height: 2.75rem;
  padding: 0 1rem;
  border-radius: 999px;
  border: 1px solid #16324f;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.mcp-console__send {
  background: #16324f;
  color: white;
}

.mcp-console__ghost {
  background: transparent;
  color: #16324f;
}

.mcp-console__send:disabled,
.mcp-console__ghost:disabled,
.mcp-console__input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

pre {
  max-width: 100%;
  margin: 0.75rem 0 0;
  padding: 0.9rem;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: 14px;
  background: #0f1d30;
  color: #eaf1ff;
  font-size: 0.82rem;
  line-height: 1.45;
}

@media (max-width: 768px) {
  .mcp-console__composer {
    grid-template-columns: 1fr;
  }

  .mcp-console__header,
  .mcp-console__draft-head,
  .mcp-console__trace-head {
    flex-direction: column;
  }

  .mcp-console__actions {
    width: 100%;
    flex-direction: column;
  }

  .mcp-console__send,
  .mcp-console__ghost {
    width: 100%;
  }
}
</style>
