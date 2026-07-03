from datetime import datetime, timezone
from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Return the current UTC date and time in ISO-8601 format."""
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        return f"Error: could not retrieve system time: {exc}"
