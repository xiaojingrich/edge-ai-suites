"""
Intent Router — lightweight request routing layer.

Routes user messages to the correct agent based on intent classification.
Replaces OpenClaw's routing function when OpenClaw is not deployed.

Two modes:
- "keyword" (default): Fast regex/keyword matching, no LLM needed
- "llm": Uses the local 7B model for intent classification (more accurate, slower)

Configuration in config.yaml:
  router:
    enabled: true
    mode: keyword  # keyword | llm
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Result of intent classification."""
    agent: str           # "report" | "homework" | "lesson_prep" | "general"
    output_format: str   # "report" | "chat"
    confidence: float    # 0.0 - 1.0


# Keyword patterns for each agent
_REPORT_PATTERNS = [
    # Chinese
    r"(生成|写|出|创建).*(报告|报表|评估)",
    r"课堂(报告|评估|分析|总结)",
    r"学情(报告|分析)",
    r"(学生|课堂).*(参与度|表现|情况)",
    r"(分析|看看|了解).*(参与|互动|课堂|教学|效果)",
    r"(出|生成|来几道).*(题|测验|quiz)",
    r"思维导图",
    r"(教师|老师).*(行为|风格|表现)",
    r"(板书|PPT).*(分析|内容)",
    r"(今天|这节|上节|本节).*(课|讲了|内容)",
    # English
    r"(generate|create|write|make).*(report|evaluation|assessment)",
    r"class(room)?\s*(report|summary|analysis|evaluation)",
    r"student\s*(engagement|participation|behavior|performance)",
    r"(analyze|check|review).*(engagement|teaching|class|lesson)",
    r"(generate|create|make).*(quiz|questions|test)",
    r"mind\s*map",
    r"teacher\s*(behavior|style|movement)",
    r"(board|ppt|slide).*(analysis|content)",
]

_REPORT_KEYWORDS_FULL_REPORT = [
    r"(完整|全面|综合).*(报告|分析|评估)",
    r"(full|complete|comprehensive).*(report|analysis|evaluation)",
    r"生成报告",
    r"generate.*report",
]

# Future: homework agent patterns
_HOMEWORK_PATTERNS = [
    r"(布置|出|生成|批改).*(作业|homework)",
    r"(homework|assignment).*(create|generate|grade|check)",
]

# Future: lesson prep agent patterns
_LESSON_PREP_PATTERNS = [
    r"(备课|教案|课程设计|教学计划)",
    r"(lesson|class).*(prep|plan|design)",
    r"(prepare|design).*(lesson|class|curriculum)",
]


def _match_patterns(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def route_by_keyword(message: str) -> RoutingResult:
    """
    Fast keyword-based intent routing.
    Returns which agent should handle the message and in what format.
    """
    # Check report agent (primary use case)
    if _match_patterns(message, _REPORT_PATTERNS):
        # Determine output_format
        if _match_patterns(message, _REPORT_KEYWORDS_FULL_REPORT):
            output_format = "report"
        else:
            output_format = "chat"

        return RoutingResult(agent="report", output_format=output_format, confidence=0.9)

    # Future: homework agent
    if _match_patterns(message, _HOMEWORK_PATTERNS):
        return RoutingResult(agent="homework", output_format="chat", confidence=0.8)

    # Future: lesson prep agent
    if _match_patterns(message, _LESSON_PREP_PATTERNS):
        return RoutingResult(agent="lesson_prep", output_format="chat", confidence=0.8)

    # Default: general chat — no data collection needed
    return RoutingResult(agent="general", output_format="chat", confidence=0.5)


def route_by_llm(message: str, model) -> RoutingResult:
    """
    LLM-based intent routing using the local 7B model.
    More accurate but slower (~2-3s).
    """
    prompt_messages = [
        {"role": "system", "content": """You are an intent classifier for a classroom AI system.
Classify the user's message into one of these categories:
- report_full: User wants a complete structured classroom report
- report_chat: User asks a specific question about class data (engagement, content, quiz, teaching analysis, etc.)
- homework: User wants to create, assign, or grade homework
- lesson_prep: User wants help preparing a lesson plan
- general: Greeting, chitchat, or any question unrelated to classroom teaching tasks

Respond with ONLY the category name, nothing else."""},
        {"role": "user", "content": message},
    ]

    prompt = model.tokenizer.apply_chat_template(
        prompt_messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    result = model.generate(prompt, stream=False, max_new_tokens=20)
    result = result.strip().lower()

    logger.info(f"[IntentRouter] LLM classification: '{result}' for message: '{message[:50]}...'")

    if "report_full" in result:
        return RoutingResult(agent="report", output_format="report", confidence=0.95)
    elif "report_chat" in result:
        return RoutingResult(agent="report", output_format="chat", confidence=0.95)
    elif "homework" in result:
        return RoutingResult(agent="homework", output_format="chat", confidence=0.9)
    elif "lesson_prep" in result:
        return RoutingResult(agent="lesson_prep", output_format="chat", confidence=0.9)
    elif "general" in result:
        return RoutingResult(agent="general", output_format="chat", confidence=0.9)
    else:
        return RoutingResult(agent="general", output_format="chat", confidence=0.5)


class IntentRouter:
    """
    Configurable intent router.

    Usage:
        router = IntentRouter(mode="keyword")
        result = router.route("生成课堂报告")
        # result.agent == "report", result.output_format == "report"
    """

    def __init__(self, mode: str = "keyword", model=None):
        """
        Args:
            mode: "keyword" (fast, no LLM) or "llm" (accurate, needs model)
            model: LLM model instance, required only when mode="llm"
        """
        self.mode = mode
        self.model = model

        if mode == "llm" and model is None:
            logger.warning("[IntentRouter] LLM mode requested but no model provided, falling back to keyword mode.")
            self.mode = "keyword"

    def route(self, message: str) -> RoutingResult:
        """Route a user message to the appropriate agent."""
        if self.mode == "llm":
            try:
                result = route_by_llm(message, self.model)
            except Exception as e:
                logger.error(f"[IntentRouter] LLM routing failed: {e}, falling back to keyword")
                result = route_by_keyword(message)
        else:
            result = route_by_keyword(message)

        logger.info(f"[IntentRouter] '{message[:40]}...' -> agent={result.agent}, "
                    f"format={result.output_format}, confidence={result.confidence:.2f}")
        return result
