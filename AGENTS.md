# LLM Layer Working Guidelines

These guidelines apply to new work in this repository, especially the thin MCP-native v1 path.

## Architecture

- Prefer thin composition layers over graph-heavy orchestration unless there is a proven need.
- Treat the external MCP server as the source of truth for operational data and actions.
- Do not re-implement tool-side business logic in the chat API unless there is a clear product reason.

## Naming

- Use domain-explicit names.
- Prefer `llm`, `mcp`, `tool`, `conversation`, `trace`, and `session` over vague terms like `data`, `handler`, or `util`.
- Use `thread_id` only at API boundaries. Inside UI code, prefer `conversationId` when the concept is conversational rather than infrastructural.
- Name files after the capability they own, not the framework primitive they happen to contain.

## Backend

- Keep request schemas, prompt builders, LLM construction, MCP access, and orchestration in separate modules.
- Prefer small classes or focused functions over files with mutable globals and mixed responsibilities.
- Convert infrastructure failures into explicit user-facing responses when safe to do so.
- Never hardcode secrets in tracked files. Local `.env` values are acceptable only because `.env` is ignored.
- Add or update tests for helper logic whenever behavior is non-trivial.

## Frontend

- Keep API types, API calls, and view state separate from the visual component when possible.
- Default to a minimal test console experience unless a richer product requirement is established.
- Preserve a clear distinction between assistant content and tool traces.

## Delivery

- Make the smallest coherent change that improves clarity, correctness, or maintainability.
- Update docs when package names, runtime requirements, or architectural assumptions change.
- Before adding features, prefer cleaning boundaries and naming in the affected area first.
