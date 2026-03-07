from __future__ import annotations

from dataclasses import dataclass, field

from radioagent.models import (
    GenerateTurnMessage,
    HistoryEntry,
    PromptConfig,
    SessionState,
    SessionUpdateMessage,
    TurnResultMessage,
    new_id,
    utc_now,
)


@dataclass(slots=True)
class DebateOrchestrator:
    agent_order: list[str] = field(default_factory=list)
    state: SessionState = field(default_factory=SessionState)

    def sync_connected_agents(self, agent_ids: list[str]) -> SessionState:
        self.state.connected_agents = agent_ids
        self.state.updated_at = utc_now()
        return self.state

    def start_debate(self, topic: str, agent_order: list[str], max_turns: int) -> SessionState:
        if len(agent_order) < 2:
            raise ValueError("At least two agents are required to start a debate")

        self.agent_order = agent_order
        now = utc_now()
        self.state = SessionState(
            session_id=new_id("session"),
            topic=topic,
            status="running",
            max_turns=max_turns,
            turn_index=0,
            next_speaker_id=agent_order[0],
            waiting_for_agent_id=None,
            connected_agents=agent_order,
            history=[
                HistoryEntry(
                    source="system",
                    speaker_id="system",
                    speaker_name="System",
                    text=f"Debate topic: {topic}",
                    created_at=now,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        return self.state

    def build_next_turn(self, prompts: dict[str, PromptConfig]) -> GenerateTurnMessage:
        if self.state.status != "running":
            raise ValueError("Debate is not running")
        if not self.state.next_speaker_id:
            raise ValueError("No next speaker is available")

        speaker_id = self.state.next_speaker_id
        prompt = prompts[speaker_id]
        next_turn_index = self.state.turn_index + 1
        self.state.waiting_for_agent_id = speaker_id
        self.state.updated_at = utc_now()
        return GenerateTurnMessage(
            session_id=self.state.session_id or "",
            topic=self.state.topic or "",
            turn_index=next_turn_index,
            speaker_id=speaker_id,
            prompt=prompt,
            history=list(self.state.history),
        )

    def apply_turn_result(self, result: TurnResultMessage) -> SessionState:
        if self.state.status != "running":
            raise ValueError("Debate is not running")
        if self.state.waiting_for_agent_id and result.speaker_id != self.state.waiting_for_agent_id:
            raise ValueError("Received turn result from unexpected agent")

        self.state.history.append(
            HistoryEntry(
                source="agent",
                speaker_id=result.speaker_id,
                speaker_name=result.speaker_name,
                text=result.text,
            )
        )
        self.state.turn_index = result.turn_index
        self.state.waiting_for_agent_id = None
        self.state.next_speaker_id = self._next_agent(result.speaker_id)
        self.state.updated_at = utc_now()

        if self.state.turn_index >= self.state.max_turns:
            self.state.status = "complete"
            self.state.next_speaker_id = None

        return self.state

    def expects_turn_result(self, session_id: str, speaker_id: str) -> bool:
        return (
            self.state.status == "running"
            and self.state.session_id == session_id
            and self.state.waiting_for_agent_id == speaker_id
        )

    def inject_user_message(self, text: str, author: str = "user") -> SessionState:
        if self.state.status == "idle":
            raise ValueError("Cannot inject a user message before the debate starts")

        interrupted_agent_id = self.state.waiting_for_agent_id
        self.state.history.append(
            HistoryEntry(
                source="user",
                speaker_id=author,
                speaker_name=author,
                text=text,
            )
        )
        if interrupted_agent_id:
            self.state.waiting_for_agent_id = None
            self.state.next_speaker_id = (
                self._next_agent(interrupted_agent_id) or interrupted_agent_id
            )
        elif not self.state.next_speaker_id and self.agent_order:
            self.state.next_speaker_id = self.agent_order[0]
        self.state.updated_at = utc_now()
        return self.state

    def session_update(self) -> SessionUpdateMessage:
        return SessionUpdateMessage(session=self.state)

    def _next_agent(self, current_agent: str) -> str | None:
        if not self.agent_order:
            return None
        if current_agent not in self.agent_order:
            return self.agent_order[0]
        current_index = self.agent_order.index(current_agent)
        next_index = (current_index + 1) % len(self.agent_order)
        return self.agent_order[next_index]

