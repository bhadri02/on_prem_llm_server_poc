from langchain_core.tools import tool

_MOCK_TEMPLATE = (
    "[POC Mock] Search results for '{query}': "
    "This is a simulated result. "
    "In production this would query an enterprise search system."
)


@tool
def web_search(query: str) -> str:
    """Search for information on a topic (simulated — no real HTTP calls made)."""
    if not query or not query.strip():
        return "Error: query must be a non-empty string"
    if len(query) > 1000:
        query = query[:1000]
    return _MOCK_TEMPLATE.format(query=query)
