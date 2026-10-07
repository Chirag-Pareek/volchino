"""Telemetry package for Hyprland window/workspace activity and idle monitoring."""

from server.telemetry.idle import IdleDetector
from server.telemetry.projects import detect_project, normalize_app_name
from server.telemetry.tracker import ActivityRecord, ActivityTracker

__all__ = [
    "ActivityRecord",
    "ActivityTracker",
    "IdleDetector",
    "detect_project",
    "normalize_app_name",
]
