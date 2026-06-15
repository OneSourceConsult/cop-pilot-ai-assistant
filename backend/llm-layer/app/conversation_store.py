from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Lock

from langchain_core.messages import BaseMessage

from app.guardrails.models import DraftSummary, ExecutionRecord


@dataclass
class ConversationState:
    messages: list[BaseMessage] = field(default_factory=list)
    pending_draft: DraftSummary | None = None
    last_execution: ExecutionRecord | None = None


class ConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, ConversationState] = {}
        self._lock = Lock()

    def get_messages(self, conversation_id: str) -> list[BaseMessage]:
        with self._lock:
            state = self._conversations.get(conversation_id)
            return list(state.messages) if state else []

    def save_messages(self, conversation_id: str, messages: Iterable[BaseMessage]) -> None:
        with self._lock:
            state = self._conversations.setdefault(conversation_id, ConversationState())
            state.messages = list(messages)

    def get_state(self, conversation_id: str) -> ConversationState:
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state is None:
                state = ConversationState()
                self._conversations[conversation_id] = state
            return ConversationState(
                messages=list(state.messages),
                pending_draft=state.pending_draft.model_copy() if state.pending_draft else None,
                last_execution=state.last_execution.model_copy() if state.last_execution else None,
            )

    def save_state(self, conversation_id: str, state: ConversationState) -> None:
        with self._lock:
            self._conversations[conversation_id] = ConversationState(
                messages=list(state.messages),
                pending_draft=state.pending_draft.model_copy() if state.pending_draft else None,
                last_execution=state.last_execution.model_copy() if state.last_execution else None,
            )

    def reset(self, conversation_id: str) -> None:
        with self._lock:
            self._conversations.pop(conversation_id, None)


conversation_store = ConversationStore()
