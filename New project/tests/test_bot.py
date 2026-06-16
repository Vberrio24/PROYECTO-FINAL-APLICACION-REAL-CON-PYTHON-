from __future__ import annotations

from datetime import date
import unittest

from src.asistente_academico.bot import (
    format_reminder_interval,
    normalize_due_date,
    normalize_priority,
    normalize_reminder_interval,
    parse_edit_task_input,
    parse_task_input,
)


class BotParsingTests(unittest.TestCase):
    def test_parse_task_with_subject_priority_and_reminder(self) -> None:
        task_input = parse_task_input(
            "Preparar sustentacion | 2026-06-15 | Programacion Avanzada | alta | cada 2 horas"
        )

        self.assertEqual(task_input.description, "Preparar sustentacion")
        self.assertEqual(task_input.due_date, "2026-06-15")
        self.assertEqual(task_input.subject, "Programacion Avanzada")
        self.assertEqual(task_input.priority, "Alta")
        self.assertEqual(task_input.reminder_minutes, 120)
        self.assertIsNone(task_input.invalid_due_date)
        self.assertIsNone(task_input.invalid_reminder)

    def test_parse_invalid_date(self) -> None:
        task_input = parse_task_input("Leer capitulo | fecha rara")

        self.assertIsNone(task_input.due_date)
        self.assertEqual(task_input.invalid_due_date, "fecha rara")

    def test_parse_invalid_reminder(self) -> None:
        task_input = parse_task_input("Leer capitulo | manana | Redes | media | cada rato")

        self.assertEqual(task_input.invalid_reminder, "cada rato")

    def test_normalize_relative_dates(self) -> None:
        today = date(2026, 6, 7)

        self.assertEqual(normalize_due_date("hoy", today), "2026-06-07")
        self.assertEqual(normalize_due_date("manana", today), "2026-06-08")
        self.assertEqual(normalize_due_date("en 3 dias", today), "2026-06-10")

    def test_normalize_spanish_month(self) -> None:
        today = date(2026, 6, 7)

        self.assertEqual(normalize_due_date("16 de junio", today), "2026-06-16")

    def test_normalize_priority(self) -> None:
        self.assertEqual(normalize_priority("urgente"), "Alta")
        self.assertEqual(normalize_priority("normal"), "Media")
        self.assertEqual(normalize_priority("baja"), "Baja")

    def test_normalize_reminder_interval(self) -> None:
        self.assertEqual(normalize_reminder_interval("sin recordatorio"), 0)
        self.assertEqual(normalize_reminder_interval("diario"), 1440)
        self.assertEqual(normalize_reminder_interval("cada 30 min"), 30)
        self.assertEqual(normalize_reminder_interval("cada 2 horas"), 120)

    def test_format_reminder_interval(self) -> None:
        self.assertEqual(format_reminder_interval(0), "sin recordatorio")
        self.assertEqual(format_reminder_interval(1440), "diario")
        self.assertEqual(format_reminder_interval(120), "cada 2 horas")

    def test_parse_edit_task_input(self) -> None:
        edit_input = parse_edit_task_input(
            "4 | Preparar demo final | 2026-06-15 | Programacion | alta | diario"
        )

        self.assertEqual(edit_input.task_id, 4)
        self.assertIsNotNone(edit_input.task_input)
        self.assertEqual(edit_input.task_input.description, "Preparar demo final")
        self.assertEqual(edit_input.task_input.subject, "Programacion")
        self.assertEqual(edit_input.task_input.priority, "Alta")


if __name__ == "__main__":
    unittest.main()
