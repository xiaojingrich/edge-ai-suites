"""
Intent Router — lightweight request routing layer.

Routes user messages to the correct agent based on intent classification.
Agents register themselves with descriptors; the router dynamically builds
its keyword patterns and LLM prompt from the registered agent list.

Two modes:
- "keyword" (default): Fast regex/keyword matching, no LLM needed
- "llm": Uses the local model for intent classification (more accurate, slower)

Hybrid strategy: keyword first, LLM fallback for ambiguous cases.

Configuration in config.yaml:
  router:
    enabled: true
    mode: keyword  # keyword | llm
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentDescriptor:
    """Describes an agent's capabilities for intent routing."""
    name: str                          # unique identifier, used as routing key
    description: str                   # one-line description for LLM prompt
    keywords: list[str] = field(default_factory=list)   # regex patterns for keyword mode
    full_report_keywords: list[str] = field(default_factory=list)  # patterns that indicate "report" output_format
    output_format: str = "chat"        # default output format


@dataclass
class RoutingResult:
    """Result of intent classification."""
    agent: str           # "report" | "homework" | "lesson_prep" | "general"
    output_format: str   # "report" | "chat"
    confidence: float    # 0.0 - 1.0


# ============================================================
# Default agent descriptors — registered at module load
# ============================================================

_REGISTERED_AGENTS: list[AgentDescriptor] = []


def register_agent(descriptor: AgentDescriptor) -> None:
    """Register an agent so the router knows it exists."""
    existing = [a for a in _REGISTERED_AGENTS if a.name == descriptor.name]
    if not existing:
        _REGISTERED_AGENTS.append(descriptor)
        logger.info(f"[IntentRouter] Registered agent: {descriptor.name}")


def get_registered_agents() -> list[AgentDescriptor]:
    return list(_REGISTERED_AGENTS)


# ============================================================
# Built-in agent registrations
# ============================================================

register_agent(AgentDescriptor(
    name="report",
    description="Classroom analysis: engagement, teaching behavior, content analysis, quiz generation, report generation",
    keywords=[
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
        r"(generate|create|write|make).*(report|evaluation|assessment)",
        r"class(room)?\s*(report|summary|analysis|evaluation)",
        r"student\s*(engagement|participation|behavior|performance)",
        r"(analyze|check|review).*(engagement|teaching|class|lesson)",
        r"(generate|create|make).*(quiz|questions|test)",
        r"mind\s*map",
        r"teacher\s*(behavior|style|movement)",
        r"(board|ppt|slide).*(analysis|content)",
        r"(engagement|participation|interaction|attention)",
        r"(which|what|when|how).*(period|time|moment|student|class|teach|engag|particip|attend)",
        r"(最|哪个|什么时候).*(参与|活跃|互动|注意力|时段|时间)",
    ],
    full_report_keywords=[
        r"(完整|全面|综合).*(报告|分析|评估)",
        r"(full|complete|comprehensive).*(report|analysis|evaluation)",
        r"生成报告",
        r"generate.*report",
    ],
    output_format="chat",
))

register_agent(AgentDescriptor(
    name="homework",
    description="Create, assign, or grade homework and assignments",
    keywords=[
        r"(布置|出|生成|批改).*(作业|homework)",
        r"(homework|assignment).*(create|generate|grade|check)",
    ],
    output_format="chat",
))

register_agent(AgentDescriptor(
    name="lesson_prep",
    description="Prepare lesson plans, curriculum design, teaching preparation",
    keywords=[
        r"(备课|教案|课程设计|教学计划)",
        r"(lesson|class).*(prep|plan|design)",
        r"(prepare|design).*(lesson|class|curriculum)",
    ],
    output_format="chat",
))

register_agent(AgentDescriptor(
    name="general",
    description="Greeting, chitchat, or any question unrelated to classroom teaching tasks",
    keywords=[],
    output_format="chat",
))


# ============================================================
# Routing implementations
# ============================================================

def _match_patterns(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def route_by_keyword(message: str) -> RoutingResult:
    """Fast keyword-based routing from registered agents."""
    for agent in _REGISTERED_AGENTS:
        if agent.name == "general":
            continue
        if agent.keywords and _match_patterns(message, agent.keywords):
            output_format = agent.output_format
            if agent.full_report_keywords and _match_patterns(message, agent.full_report_keywords):
                output_format = "report"
            return RoutingResult(agent=agent.name, output_format=output_format, confidence=0.9)

    return RoutingResult(agent="general", output_format="chat", confidence=0.5)


# ============================================================
# IntentRouter class
# ============================================================

def _build_system_prompt() -> str:
    """Build the classification system prompt once from registered agents."""
    agent_descriptions = "\n".join(
        f"- {agent.name}: {agent.description}" for agent in _REGISTERED_AGENTS
    )
    agent_names = [a.name for a in _REGISTERED_AGENTS]

    return f"""You are an intent classifier. Route the user's message to the correct agent.
Output ONLY the agent name, nothing else.

Available agents:
{agent_descriptions}

Special rule for "report" agent:
- Output "report_full" ONLY when the user's PRIMARY intent is to produce/create a new report document (e.g., "生成报告", "重新生成报告", "generate a report", "帮我出一份报告")
- Output "report" when user asks a question or requests analysis (e.g., "根据报告分析xxx", "参与度怎么样", "知识掌握情况如何", "哪些需要加强", "analyze engagement")
- Key distinction: "生成/创建/出一份 + 报告" = report_full; "分析/看看/怎么样" = report

Valid outputs: [{', '.join(agent_names)}, report_full]"""


class IntentRouter:
    """
    Configurable intent router.

    Automatically aware of all registered agents. New agents just need to call
    register_agent() and the router will include them in keyword matching and
    LLM classification without any code changes here.

    Usage:
        router = IntentRouter(mode="llm", model=model)
        result = router.route("生成课堂报告")
        # result.agent == "report", result.output_format == "report"
    """

    def __init__(self, mode: str = "keyword", model=None):
        self.mode = mode
        self.model = model

        if mode == "llm" and model is None:
            logger.warning("[IntentRouter] LLM mode requested but no model provided, falling back to keyword mode.")
            self.mode = "keyword"

        self._system_prompt = _build_system_prompt()

    def route(self, message: str) -> RoutingResult:
        """Route a user message to the appropriate agent."""
        if self.mode == "llm" and self.model:
            try:
                result = self._route_by_llm(message)
            except Exception as e:
                logger.error(f"[IntentRouter] LLM routing failed: {e}, falling back to keyword")
                result = route_by_keyword(message)
        else:
            result = route_by_keyword(message)

        logger.info(f"[IntentRouter] '{message[:40]}...' -> agent={result.agent}, "
                    f"format={result.output_format}, confidence={result.confidence:.2f}")
        return result

    def _route_by_llm(self, message: str) -> RoutingResult:
        """Call LLM with pre-built system prompt to classify intent."""
        prompt = self.model.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": message},
            ],
            tokenize=False,
            add_generation_prompt=True,
        )

        result = self.model.generate(prompt, stream=False, max_new_tokens=20)
        result = result.strip().lower()

        logger.info(f"[IntentRouter] LLM classification: '{result}' for message: '{message[:50]}...'")

        if "report_full" in result:
            return RoutingResult(agent="report", output_format="report", confidence=0.95)

        for agent in sorted(_REGISTERED_AGENTS, key=lambda a: len(a.name), reverse=True):
            if agent.name in result:
                return RoutingResult(agent=agent.name, output_format=agent.output_format, confidence=0.95)

        return RoutingResult(agent="general", output_format="chat", confidence=0.5)
