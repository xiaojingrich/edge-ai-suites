import uuid
from datetime import datetime


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
