"""
Data collector for the Report Generator.

Reads session data files (statistics, summary, mindmap, transcription, etc.)
and returns their contents in a format suitable for the LLM prompt.
"""

import os
import re
import logging
from typing import Optional

from utils.runtime_config_loader import RuntimeConfig
from utils.storage_manager import StorageManager

logger = logging.getLogger(__name__)


def _get_session_dir(session_id: str) -> str:
    project_config = RuntimeConfig.get_section("Project")
    return os.path.join(
        project_config.get("location"),
        project_config.get("name"),
        session_id,
    )


class DataCollector:
    """Reads all available session data for report generation."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.session_dir = _get_session_dir(session_id)

    def read(self, source_name: str) -> Optional[str]:
        """Read a data source by name. Returns None if data is unavailable or empty."""
        readers = {
            "class_statistics": self._read_class_statistics,
            "class_summary": self._read_class_summary,
            "mindmap": self._read_mindmap,
            "topic_segmentation": self._read_topic_segmentation,
            "teacher_transcription": self._read_teacher_transcription,
            "content_segmentation": self._read_content_segmentation,
        }

        reader = readers.get(source_name)
        if reader is None:
            logger.warning(f"[DataCollector] Unknown data source: {source_name}")
            return None

        try:
            return reader()
        except Exception as e:
            logger.error(f"[DataCollector] Failed to read {source_name}: {e}")
            return None

    def _read_class_statistics(self) -> Optional[str]:
        stats_file = os.path.join(self.session_dir, "va", "class_statistics.json")
        if not os.path.exists(stats_file):
            return None

        content = StorageManager.read_text_file(stats_file)
        if not content:
            return None

        return f"Class statistics:\n{content}"

    def _read_class_summary(self) -> Optional[str]:
        summary_path = os.path.join(self.session_dir, "summary.md")
        if not os.path.exists(summary_path):
            return None

        content = StorageManager.read_text_file(summary_path)
        if not content:
            return None

        return f"Class summary:\n{content}"

    def _read_mindmap(self) -> Optional[str]:
        mindmap_path = os.path.join(self.session_dir, "mindmap.mmd")
        if not os.path.exists(mindmap_path):
            return None

        content = StorageManager.read_text_file(mindmap_path)
        if not content:
            return None

        return f"Mind map (Mermaid format):\n{content}"

    def _read_topic_segmentation(self) -> Optional[str]:
        topics_path = os.path.join(self.session_dir, "topics.json")
        if not os.path.exists(topics_path):
            return None

        content = StorageManager.read_text_file(topics_path)
        if not content:
            return None

        return f"Topic segmentation:\n{content}"

    def _read_teacher_transcription(self) -> Optional[str]:
        path = os.path.join(self.session_dir, "teacher_transcription.txt")
        if not os.path.exists(path):
            return None

        content = StorageManager.read_text_file(path)
        if not content:
            return None

        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        total_sentences = len(lines)

        teacher_speaking_sec = 0
        total_chars = 0
        question_count = 0
        texts = []

        for line in lines:
            ts_match = re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]\s*(.*)', line)
            if ts_match:
                start = float(ts_match.group(1))
                end = float(ts_match.group(2))
                text = ts_match.group(3)
                teacher_speaking_sec += (end - start)
            else:
                text = line

            total_chars += len(text)
            texts.append(text)
            if text.endswith('？') or text.endswith('?'):
                question_count += 1

        teacher_speaking_min = teacher_speaking_sec / 60.0 if teacher_speaking_sec > 0 else 0

        total_duration_sec = 0
        cs_path = os.path.join(self.session_dir, "content_segmentation_transcription.txt")
        if os.path.exists(cs_path):
            cs_content = StorageManager.read_text_file(cs_path)
            if cs_content:
                cs_lines = cs_content.strip().split('\n')
                for cs_line in reversed(cs_lines):
                    match = re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]', cs_line.strip())
                    if match:
                        total_duration_sec = float(match.group(2))
                        break

        total_duration_min = total_duration_sec / 60.0 if total_duration_sec > 0 else 0
        speaking_speed = round(total_chars / teacher_speaking_min) if teacher_speaking_min > 0 else 0
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

        return f"Teacher transcription analysis:\n{stats}\nSample:\n{sample}"

    def _read_content_segmentation(self) -> Optional[str]:
        path = os.path.join(self.session_dir, "content_segmentation_transcription.txt")
        if not os.path.exists(path):
            return None

        content = StorageManager.read_text_file(path)
        if not content:
            return None

        lines = [l.strip() for l in content.strip().split('\n') if l.strip()]
        total_segments = len(lines)

        timestamps = []
        for line in lines:
            match = re.match(r'\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]', line)
            if match:
                timestamps.append((float(match.group(1)), float(match.group(2))))

        if timestamps:
            total_duration_sec = timestamps[-1][1] - timestamps[0][0]
            total_duration_min = total_duration_sec / 60.0

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

        return f"Content segmentation analysis:\n{stats}\nSample:\n{sample}"
