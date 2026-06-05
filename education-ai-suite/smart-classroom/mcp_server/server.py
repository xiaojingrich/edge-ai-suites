"""
Smart Classroom MCP Server

Exposes classroom session data and tools via the MCP protocol.
This is the unified interface layer for all smart-classroom capabilities
that external agents need to access.

Started from main.py alongside the main application.
"""

import os

from mcp.server.fastmcp import FastMCP
from utils.runtime_config_loader import RuntimeConfig


mcp = FastMCP(
    "smart-classroom",
    description="Smart Classroom data and tools for classroom evaluation and analysis",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_SERVER_PORT", "8100")),
)


def _get_sessions_dir() -> str:
    project_config = RuntimeConfig.get_section("Project")
    return os.path.join(
        project_config.get("location", "storage"),
        project_config.get("name", "smart-classroom"),
    )


# ============================================================
# Data Tools — read session files
# ============================================================


@mcp.tool()
def list_sessions() -> dict:
    """List all available classroom sessions with their available data files."""
    sessions_dir = _get_sessions_dir()
    if not os.path.exists(sessions_dir):
        return {"sessions": [], "error": f"Sessions directory not found: {sessions_dir}"}

    IGNORED_DIRS = {"audio"}

    sessions = []
    for entry in sorted(os.listdir(sessions_dir), reverse=True):
        session_path = os.path.join(sessions_dir, entry)
        if not os.path.isdir(session_path) or entry.startswith(".") or entry in IGNORED_DIRS:
            continue

        files = []
        for fname in os.listdir(session_path):
            fpath = os.path.join(session_path, fname)
            if os.path.isfile(fpath):
                files.append(fname)

        va_dir = os.path.join(session_path, "va")
        if os.path.isdir(va_dir):
            for fname in os.listdir(va_dir):
                if os.path.isfile(os.path.join(va_dir, fname)):
                    files.append(f"va/{fname}")

        sessions.append({"session_id": entry, "files": files})

    return {"sessions": sessions}


@mcp.tool()
def read_session_files(session_id: str, filenames: list[str]) -> dict:
    """Read one or more data files from a classroom session.

    Args:
        session_id: The session identifier (directory name).
        filenames: List of file paths relative to session directory (e.g., ["transcription.txt", "va/class_statistics.json"]).
    """
    session_dir = os.path.join(_get_sessions_dir(), session_id)
    results = {}

    for filename in filenames:
        file_path = os.path.join(session_dir, filename)

        normalized = os.path.normpath(file_path)
        if not normalized.startswith(os.path.normpath(session_dir)):
            results[filename] = {"error": "path traversal not allowed"}
            continue

        if not os.path.exists(file_path):
            results[filename] = {"error": "file not found"}
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                results[filename] = f.read()
        except Exception as e:
            results[filename] = {"error": str(e)}

    return {"session_id": session_id, "files": results}


# ============================================================
# Analytics Tools — pre-computed statistics
# ============================================================


@mcp.tool()
def get_teaching_stats(session_id: str) -> dict:
    """Get pre-computed teaching statistics for a session.

    Returns teacher speaking duration, speed, question count, class duration,
    and student engagement metrics. Use this instead of reading raw transcription
    files when you need numerical statistics.

    Args:
        session_id: The session identifier (directory name).
    """
    import re

    session_dir = os.path.join(_get_sessions_dir(), session_id)
    stats = {}

    # --- Teacher stats from teacher_transcription.txt ---
    teacher_path = os.path.join(session_dir, "teacher_transcription.txt")
    if os.path.exists(teacher_path):
        with open(teacher_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.read().strip().split("\n") if l.strip()]

        teacher_speaking_sec = 0
        teacher_chars = 0
        teacher_question_count = 0

        for line in lines:
            match = re.match(r"\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]\s*(.*)", line)
            if not match:
                continue
            start = float(match.group(1))
            end = float(match.group(2))
            text = match.group(3)
            teacher_speaking_sec += (end - start)
            teacher_chars += len(text)
            if text.endswith("？") or text.endswith("?"):
                teacher_question_count += 1

        teacher_speaking_min = teacher_speaking_sec / 60.0
        speaking_speed = round(teacher_chars / teacher_speaking_min) if teacher_speaking_min > 0 else 0

        stats["teacher_speaking_duration_sec"] = round(teacher_speaking_sec)
        stats["teacher_speaking_duration_min"] = round(teacher_speaking_min, 1)
        stats["teacher_total_chars"] = teacher_chars
        stats["teacher_speaking_speed_chars_per_min"] = speaking_speed
        stats["teacher_question_count"] = teacher_question_count
        stats["teacher_sentence_count"] = len(lines)

    # --- Class duration from content_segmentation_transcription.txt ---
    cs_path = os.path.join(session_dir, "content_segmentation_transcription.txt")
    if os.path.exists(cs_path):
        with open(cs_path, "r", encoding="utf-8") as f:
            cs_lines = f.read().strip().split("\n")

        last_end = 0
        for line in reversed(cs_lines):
            match = re.match(r"\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]", line.strip())
            if match:
                last_end = float(match.group(2))
                break

        stats["class_duration_sec"] = round(last_end)
        stats["class_duration_min"] = round(last_end / 60.0, 1)

        if "teacher_speaking_duration_sec" in stats and last_end > 0:
            stats["teacher_speaking_ratio"] = round(
                stats["teacher_speaking_duration_sec"] / last_end * 100, 1
            )

    if not stats:
        return {"error": "No data files found for this session", "session_id": session_id}

    return {"session_id": session_id, "stats": stats}
