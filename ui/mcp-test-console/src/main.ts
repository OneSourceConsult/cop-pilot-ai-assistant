import { createApp } from "vue";

import McpTestConsole from "./McpTestConsole.vue";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "http://127.0.0.1:8010";

createApp(McpTestConsole, { apiBaseUrl }).mount("#app");
