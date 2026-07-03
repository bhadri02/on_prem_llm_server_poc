# Feature: agent-framework — Unit tests for web_search tool
"""
tests/test_web_search.py

Unit tests for the web_search tool covering Requirements 8.1–8.5.

Requirements covered:
  8.1 — Tool is callable and returns a string
  8.2 — Result contains the query string as a substring
  8.3 — Query is truncated to 1000 chars before formatting
  8.4 — Empty query returns an error string
  8.5 — Whitespace-only query returns an error string
"""

import pytest

from tools.web_search import web_search


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def invoke(query: str) -> str:
    """Call the LangChain @tool via its .invoke() interface."""
    return web_search.invoke({"query": query})


# ---------------------------------------------------------------------------
# 1. Basic invocation — Requirement 8.1, 8.2
# ---------------------------------------------------------------------------


class TestWebSearchBasic:
    """Validates Requirements 8.1 and 8.2 — tool returns a string containing the query."""

    def test_returns_string(self):
        """web_search(query='hello') must return a str."""
        result = invoke("hello")
        assert isinstance(result, str)

    def test_result_contains_query(self):
        """web_search(query='hello') → result contains 'hello'."""
        result = invoke("hello")
        assert "hello" in result, (
            f"Expected 'hello' to appear in result but got: {result!r}"
        )

    def test_result_contains_arbitrary_query(self):
        """Result must contain the submitted query string as a substring."""
        query = "enterprise search test"
        result = invoke(query)
        assert query in result, (
            f"Expected {query!r} to appear in result but got: {result!r}"
        )


# ---------------------------------------------------------------------------
# 2. Empty query — Requirement 8.4
# ---------------------------------------------------------------------------


class TestWebSearchEmptyQuery:
    """Validates Requirement 8.4 — empty query returns an error string."""

    def test_empty_string_returns_error(self):
        """web_search(query='') must return an error string, not a normal result."""
        result = invoke("")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected an 'Error:' string but got: {result!r}"
        )

    def test_empty_string_is_not_normal_result(self):
        """Empty-query result must not look like a normal search result."""
        result = invoke("")
        assert "[POC Mock]" not in result, (
            "Empty query should not produce a mock result string"
        )


# ---------------------------------------------------------------------------
# 3. Whitespace-only query — Requirement 8.5
# ---------------------------------------------------------------------------


class TestWebSearchWhitespaceQuery:
    """Validates Requirement 8.5 — whitespace-only query returns an error string."""

    def test_spaces_only_returns_error(self):
        """web_search(query='   ') must return an error string."""
        result = invoke("   ")
        assert isinstance(result, str)
        assert result.startswith("Error:"), (
            f"Expected an 'Error:' string but got: {result!r}"
        )

    def test_tab_only_returns_error(self):
        """web_search(query='\\t') must return an error string."""
        result = invoke("\t")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_newline_only_returns_error(self):
        """web_search(query='\\n') must return an error string."""
        result = invoke("\n")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_mixed_whitespace_returns_error(self):
        """web_search(query=' \\t\\n ') must return an error string."""
        result = invoke(" \t\n ")
        assert isinstance(result, str)
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# 4. Query truncation — Requirement 8.3
# ---------------------------------------------------------------------------


class TestWebSearchTruncation:
    """Validates Requirement 8.3 — queries longer than 1000 chars are truncated."""

    def test_long_query_truncated_result_contains_first_1000_chars(self):
        """A query > 1000 chars is truncated; result still contains the first 1000 chars."""
        # Build a query of 1200 'a' characters followed by distinct suffix
        base = "a" * 1000
        suffix = "b" * 200
        long_query = base + suffix

        result = invoke(long_query)

        assert isinstance(result, str)
        # The first 1000 chars (all 'a's) must appear in the result
        assert base in result, (
            "Expected the first 1000 chars of the query to appear in the result"
        )

    def test_long_query_suffix_not_in_result(self):
        """The portion of the query beyond 1000 chars must NOT appear in the result."""
        base = "x" * 1000
        suffix = "UNIQUE_SUFFIX_THAT_SHOULD_BE_TRUNCATED"
        long_query = base + suffix

        result = invoke(long_query)

        assert suffix not in result, (
            f"Suffix beyond 1000 chars should have been truncated but found in: {result!r}"
        )

    def test_exactly_1000_chars_not_truncated(self):
        """A query of exactly 1000 chars must not be truncated."""
        query = "z" * 1000
        result = invoke(query)

        assert isinstance(result, str)
        assert query in result, (
            "Query of exactly 1000 chars must appear in full in the result"
        )

    def test_query_just_over_limit_truncated_correctly(self):
        """A query of 1001 chars must be truncated to 1000 chars before formatting."""
        query_1000 = "q" * 1000
        query_1001 = query_1000 + "X"  # 'X' is the 1001st character

        result = invoke(query_1001)

        # The first 1000 chars must be present
        assert query_1000 in result
        # The extra 'X' must NOT be present as part of the truncated query segment
        # (The result string contains the truncated query; the 'X' would only appear
        # if the query was not truncated.)
        # We check by ensuring the result matches the truncated form, not the full form
        assert query_1001 not in result, (
            "The untruncated 1001-char query should not appear verbatim in the result"
        )
