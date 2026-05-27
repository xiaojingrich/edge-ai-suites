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

    def _parse_action(self, llm_output: str) -> tuple[str, str]:
        """Parse the LLM output to extract Action and Action Input."""
        action_match = re.search(r"Action:\s*(.+?)(?:\n|$)", llm_output)
        input_match = re.search(r"Action Input:\s*(.+?)(?:\n|$)", llm_output)

        if not action_match:
            return None, None

        action = action_match.group(1).strip()
        action_input = input_match.group(1).strip() if input_match else "none"

        return action, action_input

    def _run_react_loop(self) -> None:
        """
        Execute the ReAct reasoning loop.
        The agent iteratively thinks, acts, and observes until it decides
        to generate the final report or hits the step limit.
        """
        logger.info(f"[ReportAgent] Starting ReAct loop for session {self.session_id}")

        history = ""

        for step in range(MAX_REACT_STEPS):
            logger.info(f"[ReportAgent] Step {step + 1}/{MAX_REACT_STEPS}")

            # Ask LLM: What should I do next?
            prompt = self._build_react_prompt(history)
            llm_response = self._call_llm_sync(prompt)

            logger.info(f"[ReportAgent] LLM response:\n{llm_response[:200]}...")

            # Record the trajectory
            self.trajectory.append(f"Step {step + 1}:\n{llm_response}")

            # Parse the action
            action, action_input = self._parse_action(llm_response)

            if action is None:
                logger.warning("[ReportAgent] Could not parse action from LLM output. Forcing generate_final_report.")
                break

            logger.info(f"[ReportAgent] Action: {action}, Input: {action_input}")

            # Execute the tool
            observation = self.tools.execute_tool(action, action_input)

            # Check if agent decided to generate report
            if observation == "__GENERATE_REPORT__":
                logger.info("[ReportAgent] Agent decided to generate final report.")
                break

            # Store observation
            self.observations.append(f"[{action}] {observation}")

            # Build history for next iteration
            history += f"{llm_response}\n{observation}\n\n"

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

        start = time.perf_counter()

        # Hold model in memory for the entire agent execution
        self.model.acquire_model()
        logger.info("[ReportAgent] Model acquired — will hold until report generation completes.")

        try:
            # Phase 1: ReAct loop — read existing data
            self._run_react_loop()

            react_time = time.perf_counter() - start
            logger.info(f"[ReportAgent] Data collection phase completed in {react_time:.2f}s")

            # Early exit: if no observations collected, no classroom data exists
            if not self.observations:
                no_data_msg = "当前无课堂记录数据，请先完成一节课的录制。" if self.language == "zh" else "No classroom recording data available. Please complete a class session first."
                logger.warning(f"[ReportAgent] No data found for session {self.session_id}")
                yield no_data_msg
                return

            # Phase 2: Generate response (streaming)
            # Choose output style: use orchestration hint if available, else keyword fallback
            if self.output_format_hint == "report":
                is_report = True
                logger.info("[ReportAgent] Intent: structured report (from orchestration hint)")
            elif self.output_format_hint == "chat":
                is_report = False
                logger.info("[ReportAgent] Intent: conversational response (from orchestration hint)")
            else:
                is_report = self._is_report_request()
                logger.info(f"[ReportAgent] Intent: {'report' if is_report else 'chat'} (local keyword detection)")

            if is_report:
                output_prompt = self._build_report_prompt()
            else:
                output_prompt = self._build_chat_prompt()

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

            # Clear report file
            StorageManager.save(report_path, "", append=False)

            first_token_time = None

            try:
                streamer = self.model.generate(output_prompt, stream=True)
                for token in streamer:
                    if first_token_time is None:
                        first_token_time = time.perf_counter()

                    StorageManager.save_async(report_path, token, append=True)
                    yield token

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