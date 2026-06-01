"""
Classroom Report Agent (学情Agent) — ReAct Architecture

A true agent that operates in a Thought → Action → Observation loop.
It autonomously decides which tools to call, collects data from multiple
sources, handles missing data gracefully, and generates a final report
only after sufficient information has been gathered.
"""

import re
import json
import time
import logging
import os

from components.base_component import PipelineComponent
from components.report_agent.tools import ToolRegistry
from components.report_agent.prompts import (
    REACT_SYSTEM_PROMPT_EN,
    REACT_SYSTEM_PROMPT_ZH,
    REPORT_GENERATION_PROMPT_EN,
    REPORT_GENERATION_PROMPT_ZH,
    CHAT_RESPONSE_PROMPT_EN,
    CHAT_RESPONSE_PROMPT_ZH,
)
from utils.config_loader import config
from utils.runtime_config_loader import RuntimeConfig
from utils.storage_manager import StorageManager
from utils.locks import audio_pipeline_lock
from utils.template_manager import get_template_path

logger = logging.getLogger(__name__)

MAX_REACT_STEPS = 6


class ReportAgent(PipelineComponent):
    """
    ReAct Agent for classroom evaluation report generation.

    Loop: Thought → Action → Observation → ... → Final Report
    """

    DEFAULT_QUERY_EN = "Generate a comprehensive full class evaluation report covering all available data."
    DEFAULT_QUERY_ZH = "生成一份完整的课堂评估报告，覆盖所有可用数据。"

    def __init__(self, session_id: str, model=None, user_query: str = None, output_format: str = None):
        self.session_id = session_id
        self.model = model
        self.language = config.app.language
        self.tools = ToolRegistry(session_id, model=model)
        self.observations = []  # collected data across steps
        self.trajectory = []    # full reasoning trace
        self.output_format_hint = output_format  # "report", "chat", or None (from orchestration layer)

        if user_query:
            self.user_query = user_query
        else:
            self.user_query = self.DEFAULT_QUERY_ZH if self.language == "zh" else self.DEFAULT_QUERY_EN

    def _get_session_dir(self) -> str:
        project_config = RuntimeConfig.get_section("Project")
        return os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            self.session_id,
        )

    def _build_react_prompt(self, history: str = "") -> str:
        """Build the ReAct system prompt with tool descriptions and user query."""
        tool_descriptions = self.tools.get_tool_descriptions()

        if self.language == "zh":
            system_prompt = REACT_SYSTEM_PROMPT_ZH.format(
                tool_descriptions=tool_descriptions,
                user_query=self.user_query,
            )
        else:
            system_prompt = REACT_SYSTEM_PROMPT_EN.format(
                tool_descriptions=tool_descriptions,
                user_query=self.user_query,
            )

        user_content = f"Session ID: {self.session_id}"

        if not history and self.observations:
            prior_summary = "\n".join(f"- {obs.split(']')[0]}]" for obs in self.observations)
            if self.language == "zh":
                user_content += f"\n\n之前对话已收集到以下数据（无需重新获取，除非你认为需要更新）：\n{prior_summary}"
            else:
                user_content += f"\n\nData already collected from previous turns (no need to re-fetch unless you think it needs updating):\n{prior_summary}"

        if history:
            user_content += f"\n\nPrevious steps:\n{history}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        return self.model.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    def _build_report_prompt(self) -> str:
        """Build the final report generation prompt with all collected observations."""
        observations_text = "\n\n---\n\n".join(self.observations)

        if self.language == "zh":
            user_content = REPORT_GENERATION_PROMPT_ZH.format(collected_observations=observations_text)
        else:
            user_content = REPORT_GENERATION_PROMPT_EN.format(collected_observations=observations_text)

        messages = [
            {"role": "system", "content": "You are a professional educational analyst. Generate the classroom evaluation report based on the provided data."},
            {"role": "user", "content": user_content},
        ]

        return self.model.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    def _is_report_request(self) -> bool:
        """Determine if the user's query is asking for a full structured report."""
        report_keywords_en = ["full report", "comprehensive report", "evaluation report",
                              "class report", "generate report", "complete report"]
        report_keywords_zh = ["完整报告", "综合报告", "评估报告", "课堂报告",
                              "生成报告", "全面报告", "学情报告"]

        query_lower = self.user_query.lower()

        if query_lower == self.DEFAULT_QUERY_EN.lower() or query_lower == self.DEFAULT_QUERY_ZH:
            return True

        for kw in report_keywords_en + report_keywords_zh:
            if kw in query_lower:
                return True

        return False

    def _build_chat_prompt(self) -> str:
        """Build a conversational response prompt for non-report queries."""
        observations_text = "\n\n---\n\n".join(self.observations) if self.observations else "No data collected."

        if self.language == "zh":
            user_content = CHAT_RESPONSE_PROMPT_ZH.format(
                user_query=self.user_query,
                collected_observations=observations_text,
            )
        else:
            user_content = CHAT_RESPONSE_PROMPT_EN.format(
                user_query=self.user_query,
                collected_observations=observations_text,
            )

        messages = [
            {"role": "system", "content": "You are a helpful classroom assistant. Answer questions based on the provided data."},
            {"role": "user", "content": user_content},
        ]

        return self.model.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    def _build_template_fill_prompt(self, template_path: str) -> str:
        """Build prompt for LLM to output replacement pairs for the template.

        LLM sees the template text + collected data, outputs lines of
        "原文 → 替换后" for each placeholder that should be filled.
        Code then applies these replacements on a copy of the .docx.
        """
        from utils.template_manager import read_template_text

        template_text = read_template_text(template_path)
        observations_text = "\n\n".join(self.observations)

        project_config = RuntimeConfig.get_section("Project")
        import time as _time
        meta_info = (
            f"学校班级: {project_config.get('school_name', '')} {project_config.get('class_name', '')}\n"
            f"课程名称: {project_config.get('course_name', '')}\n"
            f"授课教师: {project_config.get('teacher_name', '')}\n"
            f"报告时间: {_time.strftime('%Y年%m月%d日 %H:%M')}\n"
        )

        if self.language == "zh":
            user_content = f"""下面是一份课堂报告模板和收集到的课堂数据。请找出模板中需要替换的占位内容，输出替换映射。

占位内容可能是以下格式之一：
- XXX、XXXX、XX（用X表示的占位）
- {{placeholder_name}}（花括号变量名）

## 基本信息
{meta_info}

## 报告模板原文：
{template_text}

## 收集到的课堂数据：
{observations_text}

## 输出格式：
每行一条替换，用 → 分隔。左侧是模板中的【完整原文片段】，右侧是替换后的内容。
注意：左侧必须包含占位符周围的上下文文字（如"教师提问 XXX 次"），让代码能精确定位。

示例：
XXX 中学八（3）班 → 实验中学八（3）班
教师提问 XXX 次 → 教师提问 14 次
讲授时长 XXX → 讲授时长 38分钟
平均语速 XXX 字/分 → 平均语速 218 字/分
实到 XX 人 → 实到 48 人
主动举手 XX 人次 → 主动举手 96 人次

## 要求：
- 每行一条替换映射，用 → 分隔
- 左侧必须和模板原文完全一致（代码用字符串精确匹配来替换）
- 数据中没有的字段不要输出
- 不要输出任何解释"""
        else:
            user_content = f"""Below is a classroom report template and collected classroom data. Find placeholders in the template and output replacement mappings.

Placeholders may be in these formats:
- XXX, XXXX, XX (X-based placeholders)
- {{placeholder_name}} (curly brace variables)

## Basic Info
{meta_info}

## Report template:
{template_text}

## Collected classroom data:
{observations_text}

## Output format:
One replacement per line, separated by →. Left side is the EXACT text fragment from the template, right side is the replacement.
Note: Left side must include surrounding context (e.g., "Teacher questions XXX times") so the code can locate it precisely.

Examples:
Teacher questions XXX times → Teacher questions 14 times
Students present: XX → Students present: 48
Speaking speed XXX chars/min → Speaking speed 218 chars/min

## Requirements:
- One replacement mapping per line, separated by →
- Left side must exactly match the template text (code uses exact string matching)
- Do not output fields where data is unavailable
- Do not output any explanations"""

        system_msg = ("你是课堂评估报告助手。找出模板中的占位内容并输出替换映射。"
                      if self.language == "zh"
                      else "You are a classroom report assistant. Identify placeholders and output replacement mappings.")

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ]
        return self.model.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

    def _parse_actions(self, llm_output: str) -> list[tuple[str, str]]:
        """Parse LLM output to extract one or more Action calls.

        Supports two formats:
        1. Single action:
           Action: tool_name
           Action Input: input

        2. Multi-action (batch):
           Actions:
           - tool_name_1
           - tool_name_2
           - tool_name_3
        """
        actions = []

        # Try multi-action format first: "Actions:\n- tool1\n- tool2\n..."
        multi_match = re.search(r"Actions:\s*\n((?:\s*-\s*.+\n?)+)", llm_output)
        if multi_match:
            lines = multi_match.group(1).strip().split("\n")
            for line in lines:
                tool = re.sub(r"^\s*-\s*", "", line).strip()
                if tool:
                    actions.append((tool, "none"))
            if actions:
                return actions

        # Fall back to single action format
        action_match = re.search(r"Action:\s*(.+?)(?:\n|$)", llm_output)
        input_match = re.search(r"Action Input:\s*(.+?)(?:\n|$)", llm_output)

        if not action_match:
            return []

        action = action_match.group(1).strip()
        action_input = input_match.group(1).strip() if input_match else "none"
        actions.append((action, action_input))

        return actions

def _fuzzy_match_tool(self, action: str) -> str:
        """Fuzzy match an action name to a registered tool (handles 7B model typos)."""
        available = self.tools.get_available_tools()
        if action in available:
            return action

        action_lower = action.lower().strip()
        for tool in available:
            if tool.lower() == action_lower:
                return tool

        for tool in available:
            if action_lower in tool.lower() or tool.lower() in action_lower:
                return tool

        return action

    def _run_workflow_fallback(self):
        """Deterministic fallback: collect all data without LLM decision-making."""
        logger.warning("[ReportAgent] Falling back to workflow mode (ReAct failed)")

        read_tools = [
            ("get_class_statistics", "读取课堂统计" if self.language == "zh" else "Read class statistics"),
            ("get_class_summary", "读取课堂摘要" if self.language == "zh" else "Read class summary"),
            ("get_mindmap", "读取思维导图" if self.language == "zh" else "Read mind map"),
            ("get_topic_segmentation", "读取主题分段" if self.language == "zh" else "Read topic segmentation"),
            ("get_teacher_transcription", "读取教师转录" if self.language == "zh" else "Read teacher transcription"),
            ("get_content_segmentation", "读取内容分段" if self.language == "zh" else "Read content segmentation"),
        ]

        plan_steps = [{"action": t[0], "thought": t[1], "llm": False} for t in read_tools]
        plan_steps.append({"action": "generate", "thought": "生成回复" if self.language == "zh" else "Generate response", "llm": True})
        yield {"type": "plan", "steps": plan_steps}

        for i, (tool_name, _) in enumerate(read_tools):
            yield {"type": "step_start", "index": i}
            obs = self.tools.execute_tool(tool_name, "none")
            if "NOT available" not in obs and "is empty" not in obs:
                self.observations.append(f"[{tool_name}] {obs}")
            yield {"type": "step_done", "index": i}

        self.trajectory.append(f"Workflow fallback: collected {len(self.observations)} data sources")

    def _run_react_loop(self):
        """Execute the ReAct reasoning loop with multi-action support. Yields plan + step events."""
        logger.info(f"[ReportAgent] Starting ReAct loop for session {self.session_id}")

        history = ""
        plan_steps = [
            {"action": "planning", "thought": "分析需求并规划数据收集" if self.language == "zh" else "Analyze request and plan data collection", "llm": True},
        ]
        step_index = 0

        generate_label = "生成回复" if self.language == "zh" else "Generate response"
        yield {"type": "plan", "steps": plan_steps + [{"action": "generate", "thought": generate_label, "llm": True}]}
        yield {"type": "step_start", "index": 0}

        for step in range(MAX_REACT_STEPS):
            logger.info(f"[ReportAgent] Step {step + 1}/{MAX_REACT_STEPS}")

            prompt = self._build_react_prompt(history)

            try:
                llm_response = self._call_llm_sync(prompt)
            except RuntimeError as e:
                logger.error(f"[ReportAgent] LLM failed at step {step + 1}: {e}")
                if step == 0:
                    yield {"type": "step_done", "index": 0}
                self.trajectory.append(f"Step {step + 1}: LLM ERROR — {e}")
                break

            logger.info(f"[ReportAgent] LLM response:\n{llm_response[:300]}...")

            self.trajectory.append(f"Step {step + 1}:\n{llm_response}")

            thought_match = re.search(r"Thought:\s*(.+?)(?:\n|$)", llm_response)
            thought_text = thought_match.group(1).strip() if thought_match else ""

            actions = self._parse_actions(llm_response)

            if not actions:
                logger.warning("[ReportAgent] Could not parse action from LLM output. Forcing generate_final_report.")
                if step == 0:
                    yield {"type": "step_done", "index": 0}
                break

            if step == 0:
                yield {"type": "step_done", "index": 0}
                step_index = 1

            should_generate = False
            step_observations = []

            for action, action_input in actions:
                action = self._fuzzy_match_tool(action)

                if action == "generate_final_report":
                    if not self.observations:
                        logger.warning("[ReportAgent] Model tried to generate without any data — ignoring.")
                        break
                    should_generate = True
                    break

                new_step = {"action": action, "thought": thought_text, "llm": step > 0}
                plan_steps.append(new_step)

                full_plan = plan_steps + [{"action": "generate", "thought": generate_label, "llm": True}]
                yield {"type": "plan_update", "steps": full_plan}

                yield {"type": "step_start", "index": step_index}
                logger.info(f"[ReportAgent] Action: {action}, Input: {action_input}")

                observation = self.tools.execute_tool(action, action_input)

                if observation == "__GENERATE_REPORT__":
                    logger.info("[ReportAgent] Agent decided to generate final report.")
                    yield {"type": "step_done", "index": step_index}
                    step_index += 1
                    should_generate = True
                    break

                if "NOT available" not in observation and "is empty" not in observation:
                    self.observations.append(f"[{action}] {observation}")
                step_observations.append(f"Action: {action}\nObservation: {observation}")
                yield {"type": "step_done", "index": step_index}
                step_index += 1

            if should_generate:
                break

            history += f"{llm_response}\n" + "\n".join(step_observations) + "\n\n"

        logger.info(f"[ReportAgent] ReAct loop completed after {min(step + 1, MAX_REACT_STEPS)} steps. "
                    f"Collected {len(self.observations)} observations.")

    def _call_llm_sync(self, prompt: str, max_new_tokens: int = None, temperature: float = 0.3) -> str:
        """Call LLM in non-streaming mode and return full text."""
        result = self.model.generate(prompt, stream=False, max_new_tokens=max_new_tokens, temperature=temperature)
        if isinstance(result, str):
            if result.startswith("[ERROR]:"):
                raise RuntimeError(result)
            return result
        return str(result)

    def generate_report(self):
        """
        Main entry point: run the ReAct loop to collect data,
        then generate the final report via streaming LLM call.
        """
        if self.model is None:
            raise RuntimeError("ReportAgent requires a model instance.")

        # LLM service handles one request at a time; avoid queueing behind ASR
        if audio_pipeline_lock.locked():
            busy_msg = ("当前音频处理正在进行中，请等待转录/摘要完成后再使用学情Agent。"
                        if self.language == "zh"
                        else "Audio processing is in progress. Please wait for transcription/summary to complete before using the Report Agent.")
            logger.warning("[ReportAgent] audio_pipeline_lock is held, refusing to start.")
            yield {"type": "token", "content": busy_msg}
            return

        start = time.perf_counter()

        # Phase 1: Data collection — LLM decides whether to reuse or re-collect
        logger.info("[ReportAgent] Starting ReAct loop — LLM-guided tool selection")
        for event in self._run_react_loop():
            yield event

        # Fallback: if ReAct failed to collect any data, try workflow mode
        if not self.observations:
            logger.warning("[ReportAgent] ReAct collected nothing — trying workflow fallback")
            for event in self._run_workflow_fallback():
                yield event

        react_time = time.perf_counter() - start
        logger.info(f"[ReportAgent] Data collection phase completed in {react_time:.2f}s")

        # Early exit: if still no observations, no classroom data exists
        if not self.observations:
            no_data_msg = "当前无课堂记录数据，请先完成一节课的录制。" if self.language == "zh" else "No classroom recording data available. Please complete a class session first."
            logger.warning(f"[ReportAgent] No data found for session {self.session_id}")
            yield {"type": "token", "content": no_data_msg}
            return

        # Phase 2: Generate response
        if self.output_format_hint == "report":
            is_report = True
            logger.info("[ReportAgent] Intent: structured report (from orchestration hint)")
        elif self.output_format_hint == "chat":
            is_report = False
            logger.info("[ReportAgent] Intent: conversational response (from orchestration hint)")
        else:
            is_report = self._is_report_request()
            logger.info(f"[ReportAgent] Intent: {'report' if is_report else 'chat'} (local keyword detection)")

        session_dir = self._get_session_dir()
        report_path = os.path.join(session_dir, "class_report.md")
        trajectory_path = os.path.join(session_dir, "report_agent_trajectory.json")

        # Save the reasoning trajectory for transparency
        StorageManager.save(
            trajectory_path,
            json.dumps({
                "session_id": self.session_id,
                "user_query": self.user_query,
                "steps": len(self.trajectory),
                "observations_count": len(self.observations),
                "trajectory": self.trajectory,
                "observations": self.observations,
            }, ensure_ascii=False, indent=2),
            append=False,
        )

        # Check if template-based report generation should be used
        template_path = get_template_path(self.language, self.session_id) if is_report else None
        use_template = is_report and template_path is not None

        if use_template:
            output_prompt = self._build_template_fill_prompt(template_path)
            logger.info(f"[ReportAgent] Template mode: LLM fills template from collected data")
        elif is_report:
            output_prompt = self._build_report_prompt()
        else:
            output_prompt = self._build_chat_prompt()

        first_token_time = None

        if use_template:
            # Template mode: LLM outputs replacement pairs, apply on .docx copy
            from utils.template_manager import parse_replacements_from_llm, fill_template_from_text

            yield {"type": "step_start", "index": -1}
            logger.info(f"[ReportAgent] Template mode: LLM generating replacements for {template_path}")

            try:
                llm_response = self._call_llm_sync(output_prompt, max_new_tokens=2048)
            except RuntimeError as e:
                logger.error(f"[ReportAgent] LLM failed during template fill: {e}")
                err_msg = ("报告生成失败：LLM服务超时或出错，请稍后重试。"
                           if self.language == "zh"
                           else "Report generation failed. Please try again.")
                yield {"type": "token", "content": err_msg}
                yield {"type": "step_done", "index": -1}
                return

            first_token_time = time.perf_counter()
            replacements = parse_replacements_from_llm(llm_response)
            logger.info(f"[ReportAgent] LLM returned {len(replacements)} replacements")

            docx_path = os.path.join(session_dir, "class_report.docx")
            fill_template_from_text(template_path, replacements, docx_path)

            # Save only the replacement values for chat display
            summary_lines = []
            for replacement in replacements.values():
                summary_lines.append(f"- {replacement}")
            summary_text = "\n".join(summary_lines) if summary_lines else llm_response
            StorageManager.save(report_path, summary_text, append=False)

            for token in summary_text:
                yield {"type": "token", "content": token}

            yield {"type": "step_done", "index": -1}
            yield {"type": "report_ready", "session_id": self.session_id}

        elif is_report:
            # Report mode (no template): stream markdown and save to file
            StorageManager.save(report_path, "", append=False)
            yield {"type": "step_start", "index": -1}
            streamer = self.model.generate(output_prompt, stream=True)
            for token in streamer:
                if first_token_time is None:
                    first_token_time = time.perf_counter()

                StorageManager.save_async(report_path, token, append=True)
                yield {"type": "token", "content": token}

            yield {"type": "step_done", "index": -1}

        else:
            # Chat mode: stream response without overwriting report file
            yield {"type": "step_start", "index": -1}
            streamer = self.model.generate(output_prompt, stream=True)
            for token in streamer:
                if first_token_time is None:
                    first_token_time = time.perf_counter()

                yield {"type": "token", "content": token}

            yield {"type": "step_done", "index": -1}

        # Performance metrics
        end = time.perf_counter()
        total_time = end - start
        report_gen_time = end - start - react_time
        ttft = (first_token_time - start - react_time) if first_token_time else -1

        logger.info(
            f"[ReportAgent] Complete. Total: {total_time:.2f}s "
            f"(ReAct: {react_time:.2f}s, Report Gen: {report_gen_time:.2f}s, TTFT: {ttft:.2f}s)"
        )

        StorageManager.update_csv(
            path=os.path.join(session_dir, "performance_metrics.csv"),
            new_data={
                "performance.report_react_steps": len(self.trajectory),
                "performance.report_react_time": round(react_time, 4),
                "performance.report_generation_time": round(report_gen_time, 4),
                "performance.report_total_time": round(total_time, 4),
                "performance.report_ttft": f"{round(ttft, 4)}s",
            },
        )


    def process(self, _):
        """PipelineComponent interface."""
        return self.generate_report()