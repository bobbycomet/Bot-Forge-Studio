"""Guided lessons: an interactive teaching engine that works on the real Bot Forge Studio interface.

    engine     Step / Lesson model, the highlight overlay, the coach panel and the runner
    validators teaching validators for node properties (syntax, ranges, what is rejected and why)
    progress   per-user lesson progress on disk
    content    the lessons themselves — data, not code: adding a lesson never touches the engine
    hub        the lesson picker
"""
from .engine import Lesson, LessonRunner, Step
from .hub import LessonHub

__all__ = ["Lesson", "LessonRunner", "Step", "LessonHub"]
