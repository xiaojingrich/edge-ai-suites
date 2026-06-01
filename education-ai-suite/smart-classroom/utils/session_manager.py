import os
import uuid
from datetime import datetime

from utils.runtime_config_loader import RuntimeConfig


def generate_session_id():
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    short_uid = str(uuid.uuid4())[:4]  # short random suffix
    return f"{timestamp}-{short_uid}"


def get_latest_session_id() -> str | None:
    """Return the most recent session_id from in-memory SessionState."""
    from utils.session_state_manager import SessionState

    with SessionState._lock:
        if SessionState._sessions:
            return max(SessionState._sessions.keys())

    return None


def list_sessions() -> list[dict]:
    """List all sessions from the project directory (sorted newest first)."""
    project_config = RuntimeConfig.get_section("Project")
    project_dir = os.path.join(
        project_config.get("location"),
        project_config.get("name"),
    )

    if not os.path.exists(project_dir):
        return []

    sessions = []
    for name in os.listdir(project_dir):
        session_dir = os.path.join(project_dir, name)
        if not os.path.isdir(session_dir) or name.startswith("."):
            continue

        has_transcription = os.path.exists(os.path.join(session_dir, "transcription.txt"))
        has_report = os.path.exists(os.path.join(session_dir, "class_report.md"))
        has_summary = os.path.exists(os.path.join(session_dir, "summary.md"))

        sessions.append({
            "session_id": name,
            "has_transcription": has_transcription,
            "has_report": has_report,
            "has_summary": has_summary,
        })

    sessions.sort(key=lambda s: s["session_id"], reverse=True)
    return sessions
