"""
Tool definitions for the Report Agent.

Tools are organized in three categories:
1. READ tools — retrieve existing data (no side effects)
2. ACTION tools — trigger pipeline operations (ASR, summary, mindmap, etc.)
3. SKILL tools — invoke higher-level analysis skills

Each tool is a callable that the agent can invoke during its ReAct loop.
Tools return structured data that gets appended to the agent's observation context.
"""

import os
import json
import logging
from utils.runtime_config_loader import RuntimeConfig
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


def get_session_dir(session_id: str) -> str:
    project_config = RuntimeConfig.get_section("Project")
    return os.path.join(
        project_config.get("location"),
        project_config.get("name"),
        session_id,
    )


class ToolRegistry:
    """Registry of tools and skills available to the Report Agent."""

    def __init__(self, session_id: str, model=None):
        self.session_id = session_id
        self.session_dir = get_session_dir(session_id)
        self.model = model  # LLM model, passed to skills that need reasoning
        self._skill_instances = {}

    def get_tool_descriptions(self) -> str:
        return """Available Tools:

== READ (retrieve existing data, no side effects) ==
1. get_session_metadata - Check session status: available files, duration. ALWAYS call this first.
2. get_class_report - Retrieve a previously generated report (class_report.md). Use for follow-up questions.
3. get_class_statistics - Retrieve student engagement statistics (from va/class_statistics.json).
4. get_class_summary - Retrieve the class content summary (summary.md).
5. get_mindmap - Retrieve the mind map knowledge hierarchy (mindmap.mmd).
6. get_topic_segmentation - Retrieve topic-wise content segmentation (topics.json).
7. get_transcription - Retrieve the raw transcription text (transcription.txt).
8. get_teacher_transcription - Retrieve teacher-only transcription (teacher_transcription.txt). Useful for teaching behavior analysis.
9. get_content_segmentation - Retrieve content-segmented transcription (content_segmentation_transcription.txt). More structured than raw transcription.
10. get_memory - Retrieve persistent memory (historical class data, trends).

== MEMORY ==
11. save_memory - Save important findings to persistent memory for cross-session analysis.

== SKILLS (higher-level analysis combining READ data + LLM reasoning) ==
12. skill_engagement_analysis - Compute engagement score, identify patterns.
13. skill_video_slice_summary - Identify key teaching segments for video slicing.
14. skill_content_analysis - Analyze teaching objectives and knowledge coverage.
15. skill_ocr_board_analysis - Extract and analyze board/PPT content.
16. skill_quiz_generation - Generate 5 quiz questions from class content.
17. skill_teacher_behavior - Analyze teacher movement and teaching style.

== CONTROL ==
18. generate_final_report - Generate the final output. Call after collecting sufficient data.

IMPORTANT:
- This agent only READS existing data. It does NOT generate transcription, summary, or mindmap.
- If class_report.md exists in processed_files, you can answer follow-up questions from it without re-collecting all raw data.
- For a full report, collect all available READ data then call generate_final_report.
- For a specific question, collect only what you need (1-2 tools), then generate.

Tool/Skill Call Format:
Action: <name>
Action Input: <optional input or "none">"""

    def execute_tool(self, tool_name: str, tool_input: str = "none") -> str:
        """Execute a tool or skill by name and return its observation."""
        read_tools = {
            "get_class_statistics": self._get_class_statistics,
            "get_class_summary": self._get_class_summary,
            "get_class_report": self._get_class_report,
            "get_mindmap": self._get_mindmap,
            "get_session_metadata": self._get_session_metadata,
            "get_topic_segmentation": self._get_topic_segmentation,
            "get_transcription": self._get_transcription,
            "get_teacher_transcription": self._get_teacher_transcription,
            "get_content_segmentation": self._get_content_segmentation,
            "get_memory": self._get_memory,
        }

        memory_tools = {
            "save_memory": self._save_memory,
        }

        skill_map = {
            "skill_engagement_analysis": "engagement_analysis",
            "skill_video_slice_summary": "video_slice_summary",
            "skill_content_analysis": "content_analysis",
            "skill_ocr_board_analysis": "ocr_board_analysis",
            "skill_quiz_generation": "quiz_generation",
            "skill_teacher_behavior": "teacher_behavior",
        }

        # Handle skills
        if tool_name in skill_map:
            return self._execute_skill(skill_map[tool_name])

        # Handle control
        if tool_name == "generate_final_report":
            return "__GENERATE_REPORT__"

        # Handle read tools
        if tool_name in read_tools:
            try:
                return read_tools[tool_name](tool_input)
            except Exception as e:
                logger.error(f"Read tool '{tool_name}' failed: {e}")
                return f"Error executing {tool_name}: {str(e)}"

        # Handle memory tools
        if tool_name in memory_tools:
            try:
                return memory_tools[tool_name](tool_input)
            except Exception as e:
                logger.error(f"Memory tool '{tool_name}' failed: {e}")
                return f"Error executing {tool_name}: {str(e)}"

        return f"Error: Unknown tool/skill '{tool_name}'. Call get_session_metadata first."

    def _execute_skill(self, skill_name: str) -> str:
        """Execute a skill and return formatted observation."""
        from components.report_agent.skills import SKILL_REGISTRY

        if skill_name not in SKILL_REGISTRY:
            return f"Error: Unknown skill '{skill_name}'"

        # Lazy instantiation of skill
        if skill_name not in self._skill_instances:
            skill_class = SKILL_REGISTRY[skill_name]
            self._skill_instances[skill_name] = skill_class(
                session_id=self.session_id,
                tools=self,
                model=self.model,
            )

        skill = self._skill_instances[skill_name]

        try:
            result = skill.execute()
            status = result.get("status", "unknown")
            summary = result.get("summary", "")
            data = result.get("result")

            if status == "unavailable":
                return f"Observation [{skill.name}]: UNAVAILABLE — {summary}"

            data_str = json.dumps(data, ensure_ascii=False, indent=2) if data else ""
            return f"Observation [{skill.name}]: {status.upper()} — {summary}\nData:\n{data_str}"

        except Exception as e:
            logger.error(f"Skill '{skill_name}' execution failed: {e}")
            return f"Observation [{skill_name}]: ERROR — {str(e)}"

    def _get_class_statistics(self, _input: str) -> str:
        stats_file = os.path.join(self.session_dir, "va", "class_statistics.json")

        if not os.path.exists(stats_file):
            return "Observation: Class statistics (class_statistics.json) is NOT available. Video analytics may not have been run for this session."

        content = StorageManager.read_text_file(stats_file)
        if not content:
            return "Observation: class_statistics.json exists but is empty."

        return f"Observation: Class statistics retrieved successfully.\n{content}"

    def _get_class_summary(self, _input: str) -> str:
        summary_path = os.path.join(self.session_dir, "summary.md")

        if not os.path.exists(summary_path):
            return "Observation: Class summary (summary.md) is NOT available. Summarization has not been run for this session."

        content = StorageManager.read_text_file(summary_path)
        if not content:
            return "Observation: summary.md exists but is empty."

        return f"Observation: Class summary retrieved successfully.\n{content}"

    def _get_class_report(self, _input: str) -> str:
        report_path = os.path.join(self.session_dir, "class_report.md")

        if not os.path.exists(report_path):
            return "Observation: No previous report (class_report.md) exists. You need to collect raw data and generate a new report."

        content = StorageManager.read_text_file(report_path)
        if not content:
            return "Observation: class_report.md exists but is empty."

        return f"Observation: Previous class report retrieved successfully.\n{content}"

    def _get_mindmap(self, _input: str) -> str:
        mindmap_path = os.path.join(self.session_dir, "mindmap.mmd")

        if not os.path.exists(mindmap_path):
            return "Observation: Mind map (mindmap.mmd) is NOT available. Mindmap generation has not been run."

        content = StorageManager.read_text_file(mindmap_path)
        if not content:
            return "Observation: mindmap.mmd exists but is empty."

        return f"Observation: Mind map retrieved successfully.\n{content}"

    def _get_session_metadata(self, _input: str) -> str:
        from utils.session_state_manager import SessionState

        session_state = SessionState.get_session_state(self.session_id)
        audio_duration = session_state.get("audio_duration", 0)
        video_duration = session_state.get("video_duration", 0)
        has_audio = session_state.get("has_audio", False)
        has_video = session_state.get("has_video", False)

        # Check what processed output files exist
        processed_files = []
        for fname in ["transcription.txt", "teacher_transcription.txt",
                      "content_segmentation_transcription.txt",
                      "summary.md", "mindmap.mmd", "topics.json",
                      "class_report.md", "class_report.docx"]:
            if os.path.exists(os.path.join(self.session_dir, fname)):
                processed_files.append(fname)

        # Check what raw input files exist (audio/video)
        raw_media_files = []
        if os.path.exists(self.session_dir):
            for fname in os.listdir(self.session_dir):
                if any(fname.endswith(ext) for ext in [".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4", ".avi", ".mkv"]):
                    raw_media_files.append(fname)

        va_dir = os.path.join(self.session_dir, "va")
        has_va_data = os.path.exists(os.path.join(va_dir, "front_posture.txt"))

        metadata = {
            "session_id": self.session_id,
            "audio_duration_sec": audio_duration,
            "video_duration_sec": video_duration,
            "has_audio": has_audio,
            "has_video": has_video,
            "has_video_analytics": has_va_data,
            "processed_files": processed_files,
            "raw_media_files": raw_media_files,
            "session_directory": self.session_dir,
        }

        return f"Observation: Session metadata retrieved.\n{json.dumps(metadata, indent=2)}"

    def _get_topic_segmentation(self, _input: str) -> str:
        topics_path = os.path.join(self.session_dir, "topics.json")

        if not os.path.exists(topics_path):
            return "Observation: Topic segmentation (topics.json) is NOT available."

        content = StorageManager.read_text_file(topics_path)
        if not content:
            return "Observation: topics.json exists but is empty."

        return f"Observation: Topic segmentation retrieved successfully.\n{content}"

    def _get_transcription(self, _input: str) -> str:
        transcription_path = os.path.join(self.session_dir, "transcription.txt")

        if not os.path.exists(transcription_path):
            return "Observation: Transcription (transcription.txt) is NOT available. ASR transcription is produced during class — it must be completed before the agent can generate a report."

        content = StorageManager.read_text_file(transcription_path)
        if not content:
            return "Observation: transcription.txt exists but is empty."

        # Truncate if too long to fit in context
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated, full text available in file]"

        return f"Observation: Transcription retrieved successfully.\n{content}"

    def _get_teacher_transcription(self, _input: str) -> str:
        import re as _re

        path = os.path.join(self.session_dir, "teacher_transcription.txt")

        if not os.path.exists(path):
            return "Observation: Teacher transcription (teacher_transcription.txt) is NOT available."

        content = StorageManager.read_text_file(path)
        if not content:
            return "Observation: teacher_transcription.txt exists but is empty."

        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        total_sentences = len(lines)

        # Parse timestamps and extract text from each line
        # Format: [start - end] text
        teacher_speaking_sec = 0
        total_chars = 0
        question_count = 0
        texts = []

        for line in lines:
            ts_match = _re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]\s*(.*)', line)
            if ts_match:
                start = float(ts_match.group(1))
                end = float(ts_match.group(2))
                text = ts_match.group(3)
                teacher_speaking_sec += (end - start)
            else:
                # Fallback for lines without timestamps (old format)
                text = line

            total_chars += len(text)
            texts.append(text)
            if text.endswith('？') or text.endswith('?'):
                question_count += 1

        teacher_speaking_min = teacher_speaking_sec / 60.0 if teacher_speaking_sec > 0 else 0

        # Get total class duration from content_segmentation_transcription
        total_duration_sec = 0
        cs_path = os.path.join(self.session_dir, "content_segmentation_transcription.txt")
        if os.path.exists(cs_path):
            cs_content = StorageManager.read_text_file(cs_path)
            if cs_content:
                cs_lines = cs_content.strip().split('\n')
                for cs_line in reversed(cs_lines):
                    match = _re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]', cs_line.strip())
                    if match:
                        total_duration_sec = float(match.group(2))
                        break

        total_duration_min = total_duration_sec / 60.0 if total_duration_sec > 0 else 0

        # Speaking speed = teacher chars / teacher speaking time (not total time)
        speaking_speed = round(total_chars / teacher_speaking_min) if teacher_speaking_min > 0 else 0

        # Teacher speaking ratio
        speaking_ratio = round(teacher_speaking_sec / total_duration_sec * 100, 1) if total_duration_sec > 0 else 0

        stats = (
            f"--- Teacher Speech Statistics ---\n"
            f"Total sentences: {total_sentences}\n"
            f"Total characters: {total_chars}\n"
            f"Question count (sentences ending with ?): {question_count}\n"
            f"Teacher speaking duration: {teacher_speaking_sec:.0f}s ({teacher_speaking_min:.1f} min)\n"
            f"Total class duration: {total_duration_sec:.0f}s ({total_duration_min:.1f} min)\n"
            f"Teacher speaking ratio: {speaking_ratio}%\n"
            f"Speaking speed: {speaking_speed} chars/min (based on teacher speaking time)\n"
            f"---\n"
        )

        sample = "\n".join(lines[:20])
        if len(lines) > 20:
            sample += f"\n... [{len(lines) - 20} more sentences]"

        return f"Observation: Teacher transcription analyzed.\n{stats}\nSample:\n{sample}"

    def _get_content_segmentation(self, _input: str) -> str:
        import re as _re

        path = os.path.join(self.session_dir, "content_segmentation_transcription.txt")

        if not os.path.exists(path):
            return "Observation: Content segmentation transcription (content_segmentation_transcription.txt) is NOT available."

        content = StorageManager.read_text_file(path)
        if not content:
            return "Observation: content_segmentation_transcription.txt exists but is empty."

        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        total_segments = len(lines)

        timestamps = []
        for line in lines:
            match = _re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]', line)
            if match:
                timestamps.append((float(match.group(1)), float(match.group(2))))

        if timestamps:
            total_duration_sec = timestamps[-1][1] - timestamps[0][0]
            total_duration_min = total_duration_sec / 60.0

            # Analyze density per 5-minute bucket
            bucket_size = 300  # 5 minutes
            max_time = timestamps[-1][1]
            buckets = {}
            for start, end in timestamps:
                bucket_idx = int(start // bucket_size)
                buckets[bucket_idx] = buckets.get(bucket_idx, 0) + 1

            density_report = []
            for i in range(int(max_time // bucket_size) + 1):
                t_start = i * bucket_size
                t_end = min((i + 1) * bucket_size, max_time)
                count = buckets.get(i, 0)
                density_report.append(
                    f"  {t_start//60:.0f}-{t_end//60:.0f}min: {count} segments"
                )

            # Find low-density periods
            if buckets:
                min_count = min(buckets.values())
                low_periods = [f"{k*bucket_size//60:.0f}-{(k+1)*bucket_size//60:.0f}min"
                               for k, v in buckets.items() if v == min_count]
            else:
                low_periods = []

            stats = (
                f"--- Content Segmentation Statistics ---\n"
                f"Total segments: {total_segments}\n"
                f"Total duration: {total_duration_sec:.0f}s ({total_duration_min:.1f} min)\n"
                f"Time range: {timestamps[0][0]:.1f}s - {timestamps[-1][1]:.1f}s\n"
                f"Avg segment duration: {total_duration_sec/total_segments:.1f}s\n"
                f"\nDensity per 5-min period (more segments = more active):\n"
                + "\n".join(density_report) + "\n"
                f"\nLow activity periods: {', '.join(low_periods) if low_periods else 'None detected'}\n"
                f"---\n"
            )
        else:
            stats = f"--- Content Segmentation ---\nTotal lines: {total_segments}\n(No timestamps detected)\n---\n"

        sample = "\n".join(lines[:10])
        if len(lines) > 10:
            sample += f"\n... [{len(lines) - 10} more segments]"

        return f"Observation: Content segmentation analyzed.\n{stats}\nSample:\n{sample}"


    # ==================== MEMORY TOOLS ====================

    def _get_memory(self, query: str) -> str:
        """Retrieve relevant entries from persistent memory."""
        memory = AgentMemory(self.session_id)
        entries = memory.search(query if query != "none" else "")

        if not entries:
            return "Observation: No relevant memory entries found. This may be the first session."

        formatted = "\n".join(
            [f"- [{e['session_id']}] ({e['timestamp']}): {e['content']}" for e in entries[:10]]
        )
        return f"Observation: Found {len(entries)} memory entries.\n{formatted}"

    def _save_memory(self, content: str) -> str:
        """Save an observation to persistent memory for future sessions."""
        if not content or content == "none":
            return "Observation: Nothing to save — provide content in Action Input."

        memory = AgentMemory(self.session_id)
        memory.save(content)
        return f"Observation: Memory saved successfully — '{content[:80]}...'"


class AgentMemory:
    """
    Persistent memory system for cross-session knowledge.

    Stores structured memory entries as JSON lines in a shared memory file.
    Supports saving observations, searching by keyword, and retrieving history.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        project_config = RuntimeConfig.get_section("Project")
        self.memory_dir = os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            ".agent_memory",
        )
        os.makedirs(self.memory_dir, exist_ok=True)
        self.memory_file = os.path.join(self.memory_dir, "memory.jsonl")

    def save(self, content: str, category: str = "observation") -> None:
        """Append a memory entry."""
        import time as _time

        entry = {
            "session_id": self.session_id,
            "timestamp": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "content": content,
        }

        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info(f"[AgentMemory] Saved: {content[:60]}...")

    def search(self, query: str = "", limit: int = 20) -> list:
        """Search memory entries by keyword. Empty query returns recent entries."""
        if not os.path.exists(self.memory_file):
            return []

        entries = []
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            entries.append(entry)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            return []

        # Filter by query if provided
        if query:
            query_lower = query.lower()
            entries = [e for e in entries if query_lower in e.get("content", "").lower()
                       or query_lower in e.get("category", "").lower()
                       or query_lower in e.get("session_id", "").lower()]

        # Return most recent entries
        return entries[-limit:]

    def get_session_history(self) -> list:
        """Get all memory entries for the current session."""
        entries = self.search()
        return [e for e in entries if e.get("session_id") == self.session_id]