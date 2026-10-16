"""Conservative history allowance, separate from the current ReAct turn reserve."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
# Provider prefixes are stripped first. Override if the gateway imposes a smaller window.
MODEL_WINDOWS = {
    "deepseek-v4": 1_000_000,
    "deepseek-flash": 1_000_000,
    "deepseek-v3": 128_000,
    "deepseek-r1": 128_000,
    "glm-5": 200_000,
    "gpt-4o": 128_000,
}


@dataclass(frozen=True)
class ContextBudget:
    window: int
    fixed: int
    peak: int
    per_turn: int
    sliding: int
    layer1: int
    layer2: int
    healthy: bool


def compute(settings):
    name = settings.chat_model.rsplit("/", 1)[-1].lower()
    window = settings.model_context_window
    if not window:
        window = next((v for k, v in MODEL_WINDOWS.items() if name.startswith(k)), 32_000)
        if not any(name.startswith(k) for k in MODEL_WINDOWS):
            logger.warning(
                "context_budget unknown model; conservative window=32000; "
                "configure MODEL_CONTEXT_WINDOW"
            )
    fixed = (
        settings.system_prompt_tokens
        + settings.retrieval_top_k * settings.evidence_tokens_per_chunk
        + settings.summary_inject_segments * settings.summary_tokens_per_segment
        + settings.max_output_tokens
        + settings.budget_safety_margin
    )
    peak = settings.max_user_input_tokens + settings.max_agent_steps * (
        settings.tool_result_max_tokens + settings.agent_step_ai_tokens
    )
    per_turn = (
        settings.max_user_input_tokens
        + settings.max_output_tokens
        + settings.max_agent_steps * settings.layer2_tool_keep_tokens
    )
    room = max(0, window - fixed - peak)
    sliding = min(settings.context_budget_turns * per_turn, room)
    if settings.context_window_max_tokens:
        sliding = min(settings.context_window_max_tokens, room)
    layer1 = int(sliding * settings.layer1_ratio)
    return ContextBudget(
        window, fixed, peak, per_turn, sliding, layer1, sliding - layer1, room >= per_turn
    )
