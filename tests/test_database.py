from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.asistente_academico.database import TaskRepository


class TaskRepositoryTests(unittest.TestCase):
    def test_add_list_complete_and_stats(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "tasks.sqlite3"
            repository = TaskRepository(str(database_path))

            task_id = repository.add_task(
                user_id=123,
                description="Preparar demo",
                due_date=(date.today() + timedelta(days=2)).isoformat(),
                subject="Programacion Avanzada",
                priority="Alta",
                reminder_minutes=120,
            )

            tasks = repository.list_pending(123)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].id, task_id)
            self.assertEqual(tasks[0].subject, "Programacion Avanzada")
            self.assertEqual(tasks[0].priority, "Alta")
            self.assertEqual(tasks[0].reminder_minutes, 120)

            pending, done = repository.count_by_status(123)
            self.assertEqual((pending, done), (1, 0))

            self.assertTrue(repository.mark_done(123, task_id))
            pending, done = repository.count_by_status(123)
            self.assertEqual((pending, done), (0, 1))

    def test_postpone_task(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "tasks.sqlite3"
            repository = TaskRepository(str(database_path))
            tomorrow = date.today() + timedelta(days=1)

            task_id = repository.add_task(
                user_id=123,
                description="Leer articulo",
                due_date=None,
                subject="Metodologia",
                priority="Media",
            )

            self.assertTrue(repository.postpone_task(123, task_id, tomorrow))
            task = repository.list_pending(123)[0]
            self.assertEqual(task.due_date, tomorrow.isoformat())

    def test_calendar_lists_only_scheduled_tasks(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "tasks.sqlite3"
            repository = TaskRepository(str(database_path))

            repository.add_task(123, "Tarea sin fecha", None, "General", "Media")
            first_id = repository.add_task(
                123, "Primera entrega", "2026-06-10", "Redes", "Alta"
            )
            second_id = repository.add_task(
                123, "Segunda entrega", "2026-06-15", "Programacion", "Media"
            )

            tasks = repository.list_calendar(123)
            self.assertEqual([task.id for task in tasks], [first_id, second_id])

            self.assertTrue(repository.mark_done(123, first_id))
            pending_calendar = repository.list_calendar(123)
            exported_calendar = repository.list_scheduled(123, include_done=True)

            self.assertEqual([task.id for task in pending_calendar], [second_id])
            self.assertEqual([task.id for task in exported_calendar], [first_id, second_id])

    def test_reminder_respects_selected_interval(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "tasks.sqlite3"
            repository = TaskRepository(str(database_path))
            now = datetime(2026, 6, 15, 8, 0, 0)

            task_id = repository.add_task(
                123,
                "Preparar exposicion",
                "2026-06-15",
                "Programacion Avanzada",
                "Alta",
                reminder_minutes=120,
            )

            due_tasks = repository.list_tasks_for_reminder(now.date(), now.date(), now)
            self.assertEqual([task.id for task in due_tasks], [task_id])

            repository.mark_notified(task_id, now)
            too_soon = repository.list_tasks_for_reminder(
                now.date(), now.date(), now + timedelta(minutes=60)
            )
            self.assertEqual(too_soon, [])

            later = repository.list_tasks_for_reminder(
                now.date(), now.date(), now + timedelta(minutes=120)
            )
            self.assertEqual([task.id for task in later], [task_id])

    def test_update_task_and_list_overdue(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "tasks.sqlite3"
            repository = TaskRepository(str(database_path))
            today = date(2026, 6, 8)

            overdue_id = repository.add_task(
                123, "Tarea vencida", "2026-06-07", "Redes", "Alta"
            )
            week_id = repository.add_task(
                123, "Tarea editable", "2026-06-10", "Programacion Avanzada", "Media"
            )

            two_days_future = (date.today() + timedelta(days=2)).isoformat()
            self.assertTrue(
                repository.update_task(
                    123,
                    week_id,
                    "Tarea editada",
                    two_days_future,
                    "Programacion Avanzada",
                    "Alta",
                    60,
                )
            )

            edited = repository.get_task(123, week_id)
            self.assertEqual(edited.description, "Tarea editada")
            self.assertEqual(edited.priority, "Alta")

            overdue = repository.list_overdue(123, today)

            self.assertEqual([task.id for task in overdue], [overdue_id])


if __name__ == "__main__":
    unittest.main()
