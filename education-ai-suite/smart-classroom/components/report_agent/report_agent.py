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
from utils.template_manager import (
    get_template_path,
    extract_template_structure,
    build_template_fill_prompt,
    fill_template,
    parse_llm_json_response,
)

logger = logging.getLogger(__name__)

MAX_REACT_STEPS = 10


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

    def _build_report_prompt(self, use_template: bool = False, structured_stats: str = None) -> str:
        """Build the final report generation prompt with all collected observations."""
        observations_text = "\n\n---\n\n".join(self.observations)

        if use_template:
            template_path = get_template_path(self.language, self.session_id)
            if template_path:
                template_structure = extract_template_structure(template_path)
                user_content = build_template_fill_prompt(
                    template_structure, observations_text, self.language,
                    structured_stats=structured_stats,
                )
                system_msg = ("你是一个专业的教育分析师。根据提供的数据填充报告模板字段，输出JSON。"
                              if self.language == "zh"
                              else "You are a professional educational analyst. Fill report template fields based on provided data. Output JSON.")
                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_content},
                ]
                return self.model.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True,
                )

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

    def _extract_structured_stats(self) -> str:
        """Extract computed statistics from observations as structured context for the LLM.

        Instead of pre-filling template fields, we present clear numbers so
        the LLM can map them to any template format without code changes.
        """
        import time as _time

        stats_lines = []
        observations_text = "\n".join(self.observations)

        stats_lines.append(f"报告生成时间: {_time.strftime('%Y-%m-%d %H:%M')}")

        # From get_teacher_transcription
        teacher_dur_match = re.search(r"Teacher speaking duration:\s*(\d+\.?\d*)s\s*\((\d+\.?\d*)\s*min\)", observations_text)
        if teacher_dur_match:
            stats_lines.append(f"教师实际讲授时长: {teacher_dur_match.group(1)}秒 ({teacher_dur_match.group(2)}分钟)")

        total_dur_match = re.search(r"Total class duration:\s*(\d+\.?\d*)s\s*\((\d+\.?\d*)\s*min\)", observations_text)
        if total_dur_match:
            stats_lines.append(f"课堂总时长: {total_dur_match.group(1)}秒 ({total_dur_match.group(2)}分钟)")

        ratio_match = re.search(r"Teacher speaking ratio:\s*(\d+\.?\d*)%", observations_text)
        if ratio_match:
            stats_lines.append(f"教师讲授占比: {ratio_match.group(1)}%")

        speed_match = re.search(r"Speaking speed:\s*(\d+)\s*chars/min", observations_text)
        if speed_match:
            stats_lines.append(f"教师平均语速: {speed_match.group(1)} 字/分（基于教师实际发言时间）")

        question_match = re.search(r"Question count.*?:\s*(\d+)", observations_text)
        if question_match:
            stats_lines.append(f"教师提问次数: {question_match.group(1)} 次")

        sentence_match = re.search(r"Total sentences:\s*(\d+)", observations_text)
        if sentence_match:
            stats_lines.append(f"教师发言总句数: {sentence_match.group(1)} 句")

        # From get_class_statistics
        student_match = re.search(r'"student_count"\s*:\s*(\d+)', observations_text)
        if student_match:
            stats_lines.append(f"学生出勤人数: {student_match.group(1)} 人")

        raise_match = re.search(r'"raise_up_count"\s*:\s*(\d+)', observations_text)
        if raise_match:
            stats_lines.append(f"举手总次数: {raise_match.group(1)} 人次")

        stand_match = re.search(r'"stand_count"\s*:\s*(\d+)', observations_text)
        if stand_match:
            stats_lines.append(f"起立总次数: {stand_match.group(1)} 人次")

        if raise_match and student_match:
            students = int(student_match.group(1))
            raises = int(raise_match.group(1))
            avg = round(raises / students, 1) if students > 0 else 0
            stats_lines.append(f"人均举手次数: {avg} 次")

        # From get_content_segmentation
        seg_total_match = re.search(r"Total segments:\s*(\d+)", observations_text)
        if seg_total_match:
            stats_lines.append(f"内容分段总数: {seg_total_match.group(1)} 段")

        low_period_match = re.search(r"Low activity periods?:\s*(.+?)(?:\n|$)", observations_text)
        if low_period_match:
            periods = low_period_match.group(1).strip()
            if periods and periods != "None detected":
                stats_lines.append(f"低活跃时段: {periods}")

        # Density info
        density_matches = re.findall(r"(\d+-\d+min):\s*(\d+)\s*segments", observations_text)
        if density_matches:
            density_str = "; ".join([f"{m[0]}: {m[1]}段" for m in density_matches])
            stats_lines.append(f"各时段活跃度: {density_str}")

        # From get_mindmap — extract topic hierarchy as text
        mindmap_obs = [obs for obs in self.observations if "[get_mindmap]" in obs]
        if mindmap_obs:
            mmd_content = mindmap_obs[0]
            mmd_text = mmd_content.split("\n", 1)[1] if "\n" in mmd_content else ""
            try:
                mmd_json = json.loads(mmd_text.strip())
                topics = []

                def _walk_nodes(node, depth=0):
                    topic = node.get("topic", "")
                    if topic and depth <= 2:
                        prefix = "  " * depth + "- " if depth > 0 else ""
                        topics.append(f"{prefix}{topic}")
                    for child in node.get("children", []):
                        _walk_nodes(child, depth + 1)

                data = mmd_json.get("data", {})
                _walk_nodes(data)
                if topics:
                    stats_lines.append(f"思维导图知识结构:\n" + "\n".join(topics))
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        # From get_topic_segmentation — topic titles
        topic_obs = [obs for obs in self.observations if "[get_topic_segmentation]" in obs]
        if topic_obs:
            topic_text = topic_obs[0]
            try:
                json_start = topic_text.find("[")
                if json_start >= 0:
                    topics_data = json.loads(topic_text[json_start:])
                    topic_titles = [t.get("topic", "") for t in topics_data if t.get("topic")]
                    if topic_titles:
                        stats_lines.append(f"主题关键词: {'、'.join(topic_titles[:10])}")
            except (json.JSONDecodeError, TypeError):
                pass

        # teacher_name from project config
        project_config = RuntimeConfig.get_section("Project")
        teacher = project_config.get("teacher_name", "")
        if teacher:
            stats_lines.append(f"授课教师: {teacher}")

        return "\n".join(stats_lines) if stats_lines else ""

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

    def _can_use_fast_path(self) -> bool:
        """Determine if we can skip the ReAct loop entirely."""
        if self.observations:
            return False

        if self._is_report_request():
            return True

        report_path = os.path.join(self._get_session_dir(), "class_report.md")
        if os.path.exists(report_path):
            return True

        return False

    def _run_fast_collection(self):
        """Collect all available data without LLM decision-making. Yields plan + step events."""
        session_dir = self._get_session_dir()
        report_path = os.path.join(session_dir, "class_report.md")

        if not self._is_report_request() and os.path.exists(report_path):
            plan_steps = [
                {"action": "intent_analysis", "thought": "理解用户意图" if self.language == "zh" else "Understand user intent", "llm": False},
                {"action": "get_class_report", "thought": "读取已有报告" if self.language == "zh" else "Read existing report", "llm": False},
                {"action": "generate", "thought": "生成回复" if self.language == "zh" else "Generate response", "llm": True},
            ]
            yield {"type": "plan", "steps": plan_steps}
            yield {"type": "step_start", "index": 0}
            yield {"type": "step_done", "index": 0}
            yield {"type": "step_start", "index": 1}
            obs = self.tools.execute_tool("get_class_report", "none")
            self.observations.append(f"[get_class_report] {obs}")
            self.trajectory.append("Fast path: read existing report for follow-up question")
            yield {"type": "step_done", "index": 1}
            logger.info("[ReportAgent] Fast path: using existing report for follow-up")
            return

        read_tools = [
            ("get_class_statistics", "读取课堂统计数据" if self.language == "zh" else "Reading class statistics"),
            ("get_class_summary", "读取课堂摘要" if self.language == "zh" else "Reading class summary"),
            ("get_mindmap", "读取知识图谱" if self.language == "zh" else "Reading mind map"),
            ("get_topic_segmentation", "读取主题分段" if self.language == "zh" else "Reading topic segmentation"),
            ("get_teacher_transcription", "读取教师转录" if self.language == "zh" else "Reading teacher transcription"),
            ("get_content_segmentation", "读取内容分段转录" if self.language == "zh" else "Reading content segmentation"),
        ]

        template_path = get_template_path(self.language, self.session_id)
        plan_steps = [
            {"action": "intent_analysis", "thought": "理解用户意图 → 生成报告" if self.language == "zh" else "Understand intent → generate report", "llm": False},
        ]
        plan_steps += [{"action": t[0], "thought": t[1], "llm": False} for t in read_tools]
        if template_path:
            plan_steps.append({"action": "generate", "thought": "LLM 生成报告内容" if self.language == "zh" else "LLM generates report content", "llm": True})
            plan_steps.append({"action": "fill_template", "thought": "填充报告模板 → Word" if self.language == "zh" else "Fill report template → Word", "llm": False})
        else:
            plan_steps.append({"action": "generate", "thought": "基于数据生成报告" if self.language == "zh" else "Generate report from data", "llm": True})
        yield {"type": "plan", "steps": plan_steps}

        yield {"type": "step_start", "index": 0}
        yield {"type": "step_done", "index": 0}

        for i, (tool_name, desc) in enumerate(read_tools):
            yield {"type": "step_start", "index": i + 1}
            obs = self.tools.execute_tool(tool_name, "none")
            if "NOT available" not in obs and "is empty" not in obs:
                self.observations.append(f"[{tool_name}] {obs}")
            yield {"type": "step_done", "index": i + 1}

        self.trajectory.append(
            f"Fast path: collected {len(self.observations)} data sources without ReAct loop"
        )
        logger.info(f"[ReportAgent] Fast path: collected {len(self.observations)} observations (0 LLM calls)")

    def _run_react_loop(self):
        """Execute the ReAct reasoning loop with multi-action support. Yields plan + step events."""
        logger.info(f"[ReportAgent] Starting ReAct loop for session {self.session_id}")

        history = ""
        plan_steps = [
            {"action": "intent_analysis", "thought": "理解用户意图并规划" if self.language == "zh" else "Understand intent and plan", "llm": True},
        ]
        step_index = 0

        yield {"type": "plan", "steps": plan_steps + [{"action": "generate", "thought": "生成回复" if self.language == "zh" else "Generate response", "llm": True}]}
        yield {"type": "step_start", "index": 0}

        for step in range(MAX_REACT_STEPS):
            logger.info(f"[ReportAgent] Step {step + 1}/{MAX_REACT_STEPS}")

            prompt = self._build_react_prompt(history)
            llm_response = self._call_llm_sync(prompt)

            logger.info(f"[ReportAgent] LLM response:\n{llm_response[:300]}...")

            self.trajectory.append(f"Step {step + 1}:\n{llm_response}")

            thought_match = re.search(r"Thought:\s*(.+?)(?:\n|$)", llm_response)
            thought_text = thought_match.group(1).strip() if thought_match else ""

            actions = self._parse_actions(llm_response)

            if not actions:
                logger.warning("[ReportAgent] Could not parse action from LLM output. Forcing generate_final_report.")
                break

            if step == 0:
                yield {"type": "step_done", "index": 0}
                step_index = 1

            should_generate = False
            step_observations = []

            for action, action_input in actions:
                if action == "generate_final_report":
                    should_generate = True
                    break

                new_step = {"action": action, "thought": thought_text, "llm": step > 0}
                plan_steps.append(new_step)

                full_plan = plan_steps + [{"action": "generate", "thought": "基于数据生成报告" if self.language == "zh" else "Generate report from data", "llm": True}]
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

                self.observations.append(f"[{action}] {observation}")
                step_observations.append(f"Action: {action}\n{observation}")
                yield {"type": "step_done", "index": step_index}
                step_index += 1

            if should_generate:
                break

            history += f"{llm_response}\n" + "\n".join(step_observations) + "\n\n"

        logger.info(f"[ReportAgent] ReAct loop completed after {min(step + 1, MAX_REACT_STEPS)} steps. "
                    f"Collected {len(self.observations)} observations.")

    def _call_llm_sync(self, prompt: str) -> str:
        """Call LLM in non-streaming mode and return full text."""
        result = self.model.generate(prompt, stream=False)
        if isinstance(result, str):
            return result
        return str(result)

    def generate_report(self):
        """
        Main entry point: run the ReAct loop to collect data,
        then generate the final report via streaming LLM call.

        The model is held in memory for the entire duration (ReAct + report gen)
        to avoid repeated load/unload cycles on edge devices.
        """
        if self.model is None:
            raise RuntimeError("ReportAgent requires a model instance.")

        # Check if audio pipeline is using the model — avoid blocking it
        if audio_pipeline_lock.locked():
            busy_msg = ("当前音频处理正在进行中，请等待转录/摘要完成后再使用学情Agent。"
                        if self.language == "zh"
                        else "Audio processing is in progress. Please wait for transcription/summary to complete before using the Report Agent.")
            logger.warning("[ReportAgent] audio_pipeline_lock is held, refusing to start.")
            yield {"type": "token", "content": busy_msg}
            return

        start = time.perf_counter()

        # Hold model in memory for the entire agent execution
        self.model.acquire_model()
        logger.info("[ReportAgent] Model acquired — will hold until report generation completes.")

        try:
            # Phase 1: Data collection (yields thinking events)
            if self._can_use_fast_path():
                logger.info("[ReportAgent] Using fast path — skipping ReAct loop")
                for event in self._run_fast_collection():
                    yield event
            else:
                logger.info("[ReportAgent] Using ReAct loop — LLM-guided tool selection")
                for event in self._run_react_loop():
                    yield event

            react_time = time.perf_counter() - start
            logger.info(f"[ReportAgent] Data collection phase completed in {react_time:.2f}s")

            # Early exit: if no observations collected, no classroom data exists
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
                structured_stats = self._extract_structured_stats()
                logger.info(f"[ReportAgent] Extracted structured stats for LLM context")
                output_prompt = self._build_report_prompt(use_template=True, structured_stats=structured_stats)
            elif is_report:
                output_prompt = self._build_report_prompt(use_template=False)
            else:
                output_prompt = self._build_chat_prompt()

            # Clear report file
            StorageManager.save(report_path, "", append=False)

            first_token_time = None

            try:
                if use_template:
                    # Template mode: generate step (-2) then fill_template step (-1)
                    yield {"type": "step_start", "index": -2}
                    logger.info(f"[ReportAgent] Template mode: generating JSON to fill {template_path}")
                    json_response = self._call_llm_sync(output_prompt)
                    first_token_time = time.perf_counter()

                    field_values = parse_llm_json_response(json_response)
                    logger.info(f"[ReportAgent] Parsed {len(field_values)} fields from LLM response")
                    yield {"type": "step_done", "index": -2}

                    # Fill template step
                    yield {"type": "step_start", "index": -1}
                    docx_path = os.path.join(session_dir, "class_report.docx")
                    fill_template(template_path, field_values, docx_path)

                    # Also save a readable markdown summary for chat display
                    summary_lines = []
                    if self.language == "zh":
                        summary_lines.append("# 课后总结报告\n")
                    else:
                        summary_lines.append("# Post-Class Summary Report\n")

                    for key, value in field_values.items():
                        if value and value not in ("暂无数据", "Data not available"):
                            summary_lines.append(f"**{key}**: {value}\n")

                    markdown_summary = "\n".join(summary_lines)
                    StorageManager.save(report_path, markdown_summary, append=False)

                    # Stream the summary to chat
                    for token in markdown_summary:
                        yield {"type": "token", "content": token}

                    yield {"type": "step_done", "index": -1}
                    yield {"type": "report_ready", "session_id": self.session_id}

                else:
                    # Streaming mode: LLM generates markdown directly (single generate step)
                    yield {"type": "step_start", "index": -1}
                    streamer = self.model.generate(output_prompt, stream=True)
                    for token in streamer:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()

                        StorageManager.save_async(report_path, token, append=True)
                        yield {"type": "token", "content": token}

                    yield {"type": "step_done", "index": -1}

            finally:
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

        finally:
            self.model.release_model()
            logger.info("[ReportAgent] Model released.")

    def process(self, _):
        """PipelineComponent interface."""
        return self.generate_report()