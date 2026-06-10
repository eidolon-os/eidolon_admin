"""Voiceprint profile management for admin-owned user enrollment."""

from .repository import VoiceprintStore
from .router import router

__all__ = ["VoiceprintStore", "router"]
