"""Shared test settings."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_llm_cooldowns(tmp_path, monkeypatch):
    """Tests never read or write the real model cool-down file."""
    from denguewatch.agent import llm
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "llm_cooldown.json")
    monkeypatch.setattr(llm, "_cooldown", {})
    monkeypatch.setattr(llm, "_loaded", False)
