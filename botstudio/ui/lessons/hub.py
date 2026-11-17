"""The lesson picker: lists the guided lessons with difficulty, time and progress, and starts / resumes / restarts them."""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextBrowser,
                             QVBoxLayout)

from .. import theme
from . import progress
from .engine import LessonRunner

BADGE = {"Beginner": theme.GOOD, "Intermediate": theme.WARN, "Advanced": theme.BAD, "Intermediate to Advanced": theme.WARN}


class LessonHub(QDialog):
    def __init__(self, win):
        super().__init__(win)
        from .content import LESSONS
        self.win, self.lessons = win, LESSONS
        self.setWindowTitle("Guided lessons — build real bots step by step")
        self.resize(900, 560)
        self.list = QListWidget()
        self.list.setMaximumWidth(330)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.restart_btn = QPushButton("Restart from step 1")
        close = QPushButton("Close")
        self.start_btn.clicked.connect(lambda: self._start(resume=True))
        self.restart_btn.clicked.connect(lambda: self._start(resume=False))
        close.clicked.connect(self.reject)
        top = QHBoxLayout()
        top.addWidget(self.list)
        top.addWidget(self.detail, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("You do the work: each step highlights the real node, property or panel and waits until you have done it."))
        row.addStretch()
        row.addWidget(self.restart_btn)
        row.addWidget(self.start_btn)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(top, 1)
        lay.addLayout(row)
        self.list.currentRowChanged.connect(self._show)
        self._fill()

    def _fill(self):
        self.list.clear()
        for i, les in enumerate(self.lessons, 1):
            p = progress.get(les.id)
            mark = "✓ " if p.get("done") else ""
            it = QListWidgetItem(f"{mark}Lesson {i} — {les.title}\n    {les.difficulty} · ~{les.minutes} min")
            self.list.addItem(it)
        self.list.setCurrentRow(0)

    def _show(self, row: int):
        if row < 0:
            return
        les = self.lessons[row]
        p = progress.get(les.id)
        n = len(les.steps)
        state = ("**Completed** ✓" if p.get("done") else f"In progress — step {int(p.get('step', 0)) + 1} of {n}" if p.get("step") else "Not started")
        outs = "\n".join(f"- {o}" for o in les.outcomes)
        self.detail.setMarkdown(f"## {les.title}\n\n**{les.difficulty}** · about {les.minutes} minutes · {n} steps · {state}\n\n{les.summary}\n\n"
                                f"### You will learn\n{outs}\n\n" + (f"### Before you start\n{les.prerequisites}\n" if les.prerequisites else ""))
        self.start_btn.setText("Resume" if p.get("step") and not p.get("done") else "Start")
        self.restart_btn.setVisible(bool(p.get("step") or p.get("done")))

    def _start(self, resume: bool):
        row = self.list.currentRow()
        if row < 0:
            return
        les = self.lessons[row]
        fresh = not resume or not progress.get(les.id).get("step")
        if fresh and self.win.graph.nodes:
            ans = QMessageBox.question(self, "Start the lesson", "Lessons work on a project of their own. Start from a blank project?\n\n"
                                       "(You will be asked to save the current one first.)",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Yes:
                self.win.new_project()
                if self.win.graph.nodes:
                    return                    # the user cancelled the save prompt
        if not resume:
            progress.reset(les.id)
        self.accept()
        run_lesson(self.win, les, resume)


def run_lesson(win, lesson, resume: bool = True) -> LessonRunner:
    old = getattr(win, "_lesson_runner", None)
    if old is not None:
        try:
            old.close(False)
        except RuntimeError:
            pass
    runner = LessonRunner(win, lesson, resume)
    win._lesson_runner = runner
    runner.finished.connect(lambda done: setattr(win, "_lesson_runner", None))
    runner.start()
    return runner
