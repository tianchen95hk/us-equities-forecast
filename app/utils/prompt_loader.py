"""Reusable prompt template loading utility."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.exceptions import PromptLoadError


class PromptLoader:
    """Load prompt templates from a configured directory."""

    def __init__(self, prompts_dir: str):
        self._base_dir = Path(prompts_dir).resolve()

    @property
    def base_dir(self) -> Path:
        """Return base directory for prompt files."""
        return self._base_dir

    @lru_cache(maxsize=32)
    def load(self, filename: str) -> str:
        """Read a prompt file once and cache it for this process."""
        prompt_path = (self._base_dir / filename).resolve()
        if not prompt_path.exists():
            raise PromptLoadError(f"Prompt file not found: {prompt_path}")
        if prompt_path.suffix.lower() != ".txt":
            raise PromptLoadError(f"Prompt file must be .txt: {prompt_path}")
        try:
            return prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptLoadError(f"Failed to read prompt file: {prompt_path}") from exc
