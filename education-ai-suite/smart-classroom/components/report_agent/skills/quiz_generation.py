"""
Quiz Generation Skill

Generates quiz questions based on lesson content:
- 5 questions covering key concepts from the class
- Multiple choice format with answers
- Difficulty levels based on content depth
"""

import json
from components.report_agent.skills.base_skill import BaseSkill


class QuizGenerationSkill(BaseSkill):
    name = "quiz_generation"
    description = "Generate 5 quiz questions based on class content (summary + topics). Produces multiple-choice questions with answers for student assessment."

    QUIZ_PROMPT_EN = """Based on this classroom content, generate exactly 5 multiple-choice quiz questions to test student understanding.

Class Content:
{content}

Requirements:
- Generate exactly 5 questions
- Each question has 4 options (A, B, C, D)
- Include the correct answer
- Mix difficulty: 2 easy, 2 medium, 1 hard
- Questions should test understanding, not just recall

Output format (strict JSON):
[
  {{
    "id": 1,
    "question": "...",
    "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
    "answer": "B",
    "difficulty": "easy",
    "topic": "related topic"
  }}
]

Output ONLY the JSON array, nothing else."""

    QUIZ_PROMPT_ZH = """根据以下课堂内容，生成5道选择题来测试学生理解程度。

课堂内容：
{content}

要求：
- 严格生成5道题
- 每题4个选项（A、B、C、D）
- 包含正确答案
- 难度混合：2道简单、2道中等、1道困难
- 题目应测试理解能力，而非简单记忆

输出格式（严格JSON）：
[
  {{
    "id": 1,
    "question": "...",
    "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
    "answer": "B",
    "difficulty": "easy",
    "topic": "相关知识点"
  }}
]

只输出JSON数组，不要其他内容。"""

    def execute(self, context: dict = None) -> dict:
        if self.model is None:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Quiz generation requires LLM model but none is available.",
            }

        # Step 1: Get class content (prefer summary, fallback to transcription)
        summary_raw = self.tools.execute_tool("get_class_summary")
        content = ""

        if "NOT available" not in summary_raw and "empty" not in summary_raw:
            lines = summary_raw.split("\n")
            content = "\n".join(lines[1:]) if len(lines) > 1 else summary_raw
        else:
            # Fallback to transcription
            transcript_raw = self.tools.execute_tool("get_transcription")
            if "NOT available" not in transcript_raw and "empty" not in transcript_raw:
                lines = transcript_raw.split("\n")
                content = "\n".join(lines[1:]) if len(lines) > 1 else transcript_raw

        if not content:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "No class content available to generate quiz questions.",
            }

        # Step 2: Generate quiz via LLM
        from utils.config_loader import config
        if config.app.language == "zh":
            prompt = self.QUIZ_PROMPT_ZH.format(content=content[:3000])
        else:
            prompt = self.QUIZ_PROMPT_EN.format(content=content[:3000])

        llm_response = self._call_llm(prompt)

        # Step 3: Parse quiz JSON
        quiz = []
        try:
            # Try to extract JSON from response
            json_start = llm_response.find("[")
            json_end = llm_response.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                quiz = json.loads(llm_response[json_start:json_end])
        except json.JSONDecodeError:
            return {
                "status": "partial",
                "result": {"raw_response": llm_response[:1000]},
                "summary": "Quiz generated but JSON parsing failed. Raw response preserved.",
            }

        result = {
            "quiz_count": len(quiz),
            "questions": quiz,
        }

        return {
            "status": "success",
            "result": result,
            "summary": f"Generated {len(quiz)} quiz questions covering class content.",
        }