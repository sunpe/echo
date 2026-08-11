"""Pure construction of app-server turn requests."""

from typing import NamedTuple, Optional


class TurnContext(NamedTuple):
    thread_id: str
    active_turn_id: Optional[str]
    model: Optional[str]
    developer_instructions: Optional[str]
    planning: bool


def build_turn_params(context, text, proceed_plan=False):
    mode = "default" if proceed_plan or not context.planning else "plan"
    params = {
        "threadId": context.thread_id,
        "input": [{"type": "text", "text": text}],
        "collaborationMode": {
            "mode": mode,
            "settings": {
                "model": context.model,
                "reasoning_effort": None,
                "developer_instructions": context.developer_instructions,
            },
        },
    }
    if context.active_turn_id:
        params["expectedTurnId"] = context.active_turn_id
    if context.model:
        params["model"] = context.model
    return params


def rollback_count(turn_count, target_index):
    """Number of trailing turns removed when keeping target_index - 1 turns."""
    return max(0, int(turn_count) - (int(target_index) - 1))
