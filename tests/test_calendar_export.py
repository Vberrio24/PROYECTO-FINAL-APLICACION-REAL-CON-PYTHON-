from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.asistente_academico.calendar_export import (
    build_html_calendar,
    write_user_calendar_html,
)
from src.asistente_academico.database import Task


class CalendarExportTests(unittest.TestCase):
    def test_build_html_calendar_contains_month_grid(self) -> None:
        task = Task(
            id=4,
            user_id=123,
            description="Presentar proyecto",
            due_date="2026-06-16",
            subject="Programacion Avanzada",
            priority="Alta",
            reminder_minutes=60,
            last_notified_at=None,
            is_done=False,
        )

        html = build_html_calendar([task], today=date(2026, 6, 8))

        self.assertIn("<title>Calendario Academico</title>", html)
        self.assertIn("Presentar proyecto", html)
        self.assertIn("Programacion Avanzada", html)
        self.assertIn("priority-alta", html)
        self.assertIn("<div>Lunes</div>", html)
        self.assertIn("<div>Domingo</div>", html)

    def test_build_html_calendar_renders_multiple_months(self) -> None:
        first_task = Task(
            id=6,
            user_id=123,
            description="Primera entrega",
            due_date="2026-06-16",
            subject="Programacion",
            priority="Alta",
            reminder_minutes=60,
            last_notified_at=None,
            is_done=False,
        )
        second_task = Task(
            id=7,
            user_id=123,
            description="Entrega final",
            due_date="2026-07-03",
            subject="Redes",
            priority="Media",
            reminder_minutes=1440,
            last_notified_at=None,
            is_done=False,
        )

        html = build_html_calendar([first_task, second_task], today=date(2026, 6, 8))

        self.assertIn("Junio 2026", html)
        self.assertIn("Julio 2026", html)
        self.assertIn("Primera entrega", html)
        self.assertIn("Entrega final", html)

    def test_write_user_calendar_html_creates_file(self) -> None:
        task = Task(
            id=5,
            user_id=123,
            description="Resolver taller",
            due_date="2026-06-18",
            subject="Redes",
            priority="Media",
            reminder_minutes=1440,
            last_notified_at=None,
            is_done=False,
        )

        with TemporaryDirectory() as directory:
            calendar_path = write_user_calendar_html(Path(directory), 123, [task])

            self.assertTrue(calendar_path.exists())
            self.assertEqual(calendar_path.name, "calendario_123.html")
            self.assertIn("Resolver taller", calendar_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
