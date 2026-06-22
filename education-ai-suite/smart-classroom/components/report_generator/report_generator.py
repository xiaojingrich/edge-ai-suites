"""
Deterministic Report Generator for Classroom Evaluation.

Collects all available session data (statistics, summary, mindmap, transcription, etc.)
and passes it to the LLM to generate a structured report. No autonomous decision-making,
no ReAct loop — just a fixed pipeline: collect data → generate report.
"""

import time
import json
import logging
import os

from components.report_generator.data_collector import DataCollector
from components.report_generator.prompts import (
    REPORT_GENERATION_PROMPT_EN,
    REPORT_GENERATION_PROMPT_ZH,
    TEMPLATE_FILL_SYSTEM_EN,
    TEMPLATE_FILL_SYSTEM_ZH,
    TEMPLATE_FILL_PROMPT_EN,
    TEMPLATE_FILL_PROMPT_ZH,
)
from utils.config_loader import config
from utils.runtime_config_loader import RuntimeConfig
from utils.storage_manager import StorageManager
from utils.locks import audio_pipeline_lock
from utils.template_manager import (
    get_template_path,
    extract_template_structure,
    fill_template,
    parse_llm_json_response,
)

logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    Deterministic report generator.

    Pipeline: Collect all available data → Build prompt → Stream LLM output.
    """

    def __init__(self, session_id: str, model=None):
        self.session_id = session_id
        self.model = model
        self.language = config.app.language
        self.data_collector = DataCollector(session_id)
        self.collected_data = []

    def _get_session_dir(self) -> str:
        project_config = RuntimeConfig.get_section("Project")
        return os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            self.session_id,
        )

    def _collect_all_data(self):
        """Deterministically collect all available session data."""
        data_sources = [
            ("class_statistics", "读取课堂统计" if self.language == "zh" else "Read class statistics"),
            ("class_summary", "读取课堂摘要" if self.language == "zh" else "Read class summary"),
            ("mindmap", "读取思维导图" if self.language == "zh" else "Read mind map"),
            ("topic_segmentation", "读取主题分段" if self.language == "zh" else "Read topic segmentation"),
            ("teacher_transcription", "读取教师转录" if self.language == "zh" else "Read teacher transcription"),
            ("content_segmentation", "读取内容分段" if self.language == "zh" else "Read content segmentation"),
        ]

        plan_steps = [{"action": s[0], "thought": s[1], "llm": False} for s in data_sources]
        plan_steps.append({
            "action": "generate",
            "thought": "生成报告" if self.language == "zh" else "Generate report",
            "llm": True,
        })
        yield {"type": "plan", "steps": plan_steps}

        for i, (source_name, _) in enumerate(data_sources):
            yield {"type": "step_start", "index": i}
            result = self.data_collector.read(source_name)
            if result is not None:
                self.collected_data.append(f"[{source_name}] {result}")
            yield {"type": "step_done", "index": i}

    def _build_report_prompt(self, use_template: bool = False) -> str:
        """Build the LLM prompt from collected data."""
        import json as _json

        observations_text = "\n\n---\n\n".join(self.collected_data)

        if use_template:
            template_path = get_template_path(self.language, self.session_id)
            if template_path:
                template_structure = extract_template_structure(template_path)
                fields_json = _json.dumps(template_structure["all_fields"], ensure_ascii=False)

                if self.language == "zh":
                    system_msg = TEMPLATE_FILL_SYSTEM_ZH
                    user_content = TEMPLATE_FILL_PROMPT_ZH.format(
                        template_raw_text=template_structure["raw_text"],
                        collected_data=observations_text,
                        fields_json=fields_json,
                    )
                else:
                    system_msg = TEMPLATE_FILL_SYSTEM_EN
                    user_content = TEMPLATE_FILL_PROMPT_EN.format(
                        template_raw_text=template_structure["raw_text"],
                        collected_data=observations_text,
                        fields_json=fields_json,
                    )

                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_content},
                ]
                return self.model.tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True,
                )

        if self.language == "zh":
            user_content = REPORT_GENERATION_PROMPT_ZH.format(collected_data=observations_text)
        else:
            user_content = REPORT_GENERATION_PROMPT_EN.format(collected_data=observations_text)

        messages = [
            {"role": "system", "content": "You are a professional educational analyst. Generate the classroom evaluation report based on the provided data."},
            {"role": "user", "content": user_content},
        ]

        return self.model.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
        )

    def generate_report(self):
        """
        Main entry point. Collects data then generates the report via streaming LLM.
        Yields events: plan, step_start, step_done, token, report_ready.
        """
        if self.model is None:
            raise RuntimeError("ReportGenerator requires a model instance.")

        if audio_pipeline_lock.locked():
            busy_msg = (
                "当前音频处理正在进行中，请等待转录/摘要完成后再生成报告。"
                if self.language == "zh"
                else "Audio processing is in progress. Please wait for transcription/summary to complete."
            )
            logger.warning("[ReportGenerator] audio_pipeline_lock is held, refusing to start.")
            yield {"type": "token", "content": busy_msg}
            return

        start = time.perf_counter()

        # Phase 1: Collect all available data
        for event in self._collect_all_data():
            yield event

        collect_time = time.perf_counter() - start
        logger.info(f"[ReportGenerator] Data collection completed in {collect_time:.2f}s, "
                    f"collected {len(self.collected_data)} sources")

        if not self.collected_data:
            no_data_msg = (
                "当前无课堂记录数据，请先完成一节课的录制。"
                if self.language == "zh"
                else "No classroom recording data available. Please complete a class session first."
            )
            logger.warning(f"[ReportGenerator] No data found for session {self.session_id}")
            yield {"type": "token", "content": no_data_msg}
            return

        # Phase 2: Generate report
        session_dir = self._get_session_dir()
        report_path = os.path.join(session_dir, "class_report.md")

        template_path = get_template_path(self.language, self.session_id)
        use_template = template_path is not None

        output_prompt = self._build_report_prompt(use_template=use_template)
        first_token_time = None

        if use_template:
            yield {"type": "step_start", "index": -1}
            logger.info(f"[ReportGenerator] Template mode: generating JSON to fill {template_path}")

            try:
                json_response = self.model.generate(output_prompt, stream=False, max_new_tokens=2048, temperature=0.3)
                if isinstance(json_response, str) and json_response.startswith("[ERROR]:"):
                    raise RuntimeError(json_response)
            except RuntimeError as e:
                logger.error(f"[ReportGenerator] LLM failed during template fill: {e}")
                err_msg = (
                    "报告生成失败：LLM服务超时或出错，请稍后重试。"
                    if self.language == "zh"
                    else "Report generation failed. Please try again."
                )
                yield {"type": "token", "content": err_msg}
                yield {"type": "step_done", "index": -1}
                return

            first_token_time = time.perf_counter()
            field_values = parse_llm_json_response(json_response)
            logger.info(f"[ReportGenerator] Parsed {len(field_values)} fields from LLM response")

            docx_path = os.path.join(session_dir, "class_report.docx")
            fill_template(template_path, field_values, docx_path)

            from utils.template_manager import read_docx_as_markdown
            markdown_content = read_docx_as_markdown(docx_path)
            StorageManager.save(report_path, markdown_content, append=False)

            for token in markdown_content:
                yield {"type": "token", "content": token}

            yield {"type": "step_done", "index": -1}
            yield {"type": "report_ready", "session_id": self.session_id}

        else:
            # No template: stream markdown directly
            StorageManager.save(report_path, "", append=False)
            yield {"type": "step_start", "index": -1}

            streamer = self.model.generate(output_prompt, stream=True)
            for token in streamer:
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                StorageManager.save_async(report_path, token, append=True)
                yield {"type": "token", "content": token}

            yield {"type": "step_done", "index": -1}
            yield {"type": "report_ready", "session_id": self.session_id}

        # Performance metrics
        end = time.perf_counter()
        total_time = end - start
        generation_time = end - start - collect_time
        ttft = (first_token_time - start - collect_time) if first_token_time else -1

        logger.info(
            f"[ReportGenerator] Complete. Total: {total_time:.2f}s "
            f"(Collect: {collect_time:.2f}s, Generate: {generation_time:.2f}s, TTFT: {ttft:.2f}s)"
        )

        StorageManager.update_csv(
            path=os.path.join(session_dir, "performance_metrics.csv"),
            new_data={
                "performance.report_collect_time": round(collect_time, 4),
                "performance.report_generation_time": round(generation_time, 4),
                "performance.report_total_time": round(total_time, 4),
                "performance.report_ttft": f"{round(ttft, 4)}s",
            },
        )
