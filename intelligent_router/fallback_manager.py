"""
intelligent_router/fallback_manager.py

Fallback chain traversal for the Intelligent Router (Layer 3).

Provides:
  - FallbackState: dataclass tracking position within a fallback chain.
  - create_fallback_state: factory that builds a FallbackState from a primary
    model name and a loaded ModelMatrix.
"""

from dataclasses import dataclass
from typing import Optional

from intelligent_router.model_selector import ModelMatrix, get_fallback_chain


@dataclass
class FallbackState:
    """Tracks the current position within an ordered fallback chain.

    Attributes:
        chain:          Ordered list of model names to try, starting with the
                        primary model.
        current_index:  Index into ``chain`` of the currently selected model.
        fallback_level: Number of times ``advance()`` has been called
                        successfully; always equals ``current_index``.
    """

    chain: list[str]
    current_index: int
    fallback_level: int

    @property
    def selected_model(self) -> str:
        """Return the model name at the current position in the chain."""
        return self.chain[self.current_index]

    @property
    def has_next(self) -> bool:
        """Return True if there is at least one more model after the current one."""
        return self.current_index + 1 < len(self.chain)

    def advance(self) -> Optional[str]:
        """Advance to the next model in the chain.

        Increments both ``current_index`` and ``fallback_level`` by exactly 1.

        Returns:
            The new model name if a next model exists, or ``None`` if the chain
            is exhausted.
        """
        if not self.has_next:
            return None
        self.current_index += 1
        self.fallback_level += 1
        return self.chain[self.current_index]


def create_fallback_state(primary_model: str, matrix: ModelMatrix) -> FallbackState:
    """Build a :class:`FallbackState` starting from *primary_model*.

    Args:
        primary_model: Name of the primary (first) model to try.
        matrix:        The loaded :class:`ModelMatrix` used to resolve the
                       fallback chain.

    Returns:
        A :class:`FallbackState` with ``current_index=0`` and
        ``fallback_level=0``, ready for use in the routing pipeline.
    """
    chain = get_fallback_chain(primary_model, matrix)
    return FallbackState(chain=chain, current_index=0, fallback_level=0)
