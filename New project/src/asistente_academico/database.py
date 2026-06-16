from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Task:
    id: int
    user_id: int
    description: str
    due_date: str | None
    subject: str
    priority: str
    reminder_minutes: int
    last_notified_at: str | None
    is_done: bool


class TaskRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    due_date TEXT,
                    subject TEXT NOT NULL DEFAULT 'General',
                    priority TEXT NOT NULL DEFAULT 'Media',
                    reminder_minutes INTEGER NOT NULL DEFAULT 1440,
                    is_done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    last_notified_for TEXT,
                    last_notified_at TEXT
                )
                """
            )
            self._ensure_column(connection, "tasks", "last_notified_for", "TEXT")
            self._ensure_column(connection, "tasks", "last_notified_at", "TEXT")
            self._ensure_column(
                connection, "tasks", "subject", "TEXT NOT NULL DEFAULT 'General'"
            )
            self._ensure_column(
                connection, "tasks", "priority", "TEXT NOT NULL DEFAULT 'Media'"
            )
            self._ensure_column(
                connection, "tasks", "reminder_minutes", "INTEGER NOT NULL DEFAULT 1440"
            )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(column["name"] == column_name for column in columns):
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )

    def add_task(
        self,
        user_id: int,
        description: str,
        due_date: str | None,
        subject: str,
        priority: str,
        reminder_minutes: int = 1440,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks (
                    user_id, description, due_date, subject, priority, reminder_minutes
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, description, due_date, subject, priority, reminder_minutes),
            )
            return int(cursor.lastrowid)

    def get_task(self, user_id: int, task_id: int) -> Task | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id, user_id, description, due_date, subject, priority,
                    reminder_minutes, last_notified_at, is_done
                FROM tasks
                WHERE id = ? AND user_id = ?
                """,
                (task_id, user_id),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task(
        self,
        user_id: int,
        task_id: int,
        description: str,
        due_date: str | None,
        subject: str,
        priority: str,
        reminder_minutes: int,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET description = ?,
                    due_date = ?,
                    subject = ?,
                    priority = ?,
                    reminder_minutes = ?,
                    last_notified_for = NULL,
                    last_notified_at = NULL
                WHERE id = ? AND user_id = ? AND is_done = 0
                """,
                (
                    description,
                    due_date,
                    subject,
                    priority,
                    reminder_minutes,
                    task_id,
                    user_id,
                ),
            )
            return cursor.rowcount > 0

    def list_pending(self, user_id: int) -> list[Task]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id, user_id, description, due_date, subject, priority,
                    reminder_minutes, last_notified_at, is_done
                FROM tasks
                WHERE user_id = ? AND is_done = 0
                ORDER BY
                    COALESCE(due_date, '9999-12-31'),
                    CASE priority
                        WHEN 'Alta' THEN 1
                        WHEN 'Media' THEN 2
                        ELSE 3
                    END,
                    id
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def mark_done(self, user_id: int, task_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET is_done = 1, completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND is_done = 0
                """,
                (task_id, user_id),
            )
            return cursor.rowcount > 0

    def delete_task(self, user_id: int, task_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
            return cursor.rowcount > 0

    def postpone_task(self, user_id: int, task_id: int, due_date: date) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET due_date = ?, last_notified_for = NULL, last_notified_at = NULL
                WHERE id = ? AND user_id = ? AND is_done = 0
                """,
                (due_date.isoformat(), task_id, user_id),
            )
            return cursor.rowcount > 0

    def list_calendar(self, user_id: int) -> list[Task]:
        return self.list_scheduled(user_id, include_done=False)

    def list_overdue(self, user_id: int, today: date) -> list[Task]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id, user_id, description, due_date, subject, priority,
                    reminder_minutes, last_notified_at, is_done
                FROM tasks
                WHERE user_id = ? AND is_done = 0 AND due_date < ?
                ORDER BY due_date, id
                """,
                (user_id, today.isoformat()),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_scheduled(self, user_id: int, include_done: bool = False) -> list[Task]:
        done_filter = "" if include_done else "AND is_done = 0"
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id, user_id, description, due_date, subject, priority,
                    reminder_minutes, last_notified_at, is_done
                FROM tasks
                WHERE user_id = ? AND due_date IS NOT NULL
                {done_filter}
                ORDER BY
                    due_date,
                    CASE priority
                        WHEN 'Alta' THEN 1
                        WHEN 'Media' THEN 2
                        ELSE 3
                    END,
                    subject,
                    id
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def count_by_status(self, user_id: int) -> tuple[int, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT is_done, COUNT(*) AS total
                FROM tasks
                WHERE user_id = ?
                GROUP BY is_done
                """,
                (user_id,),
            ).fetchall()

        pending = 0
        done = 0
        for row in rows:
            if row["is_done"]:
                done = row["total"]
            else:
                pending = row["total"]
        return pending, done

    def count_overdue(self, user_id: int, today: date) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM tasks
                WHERE user_id = ? AND is_done = 0 AND due_date < ?
                """,
                (user_id, today.isoformat()),
            ).fetchone()
        return int(row["total"])

    def count_pending_by_subject(self, user_id: int) -> list[tuple[str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT subject, COUNT(*) AS total
                FROM tasks
                WHERE user_id = ? AND is_done = 0
                GROUP BY subject
                ORDER BY total DESC, subject
                LIMIT 5
                """,
                (user_id,),
            ).fetchall()
        return [(row["subject"], int(row["total"])) for row in rows]

    def list_tasks_for_reminder(
        self, today: date, tomorrow: date, now: datetime
    ) -> list[Task]:
        today_text = today.isoformat()
        tomorrow_text = tomorrow.isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id, user_id, description, due_date, subject, priority,
                    reminder_minutes, last_notified_at, is_done
                FROM tasks
                WHERE is_done = 0
                  AND due_date IN (?, ?)
                  AND reminder_minutes > 0
                ORDER BY due_date, id
                """,
                (today_text, tomorrow_text),
            ).fetchall()
        return [
            self._row_to_task(row)
            for row in rows
            if self._should_notify(row["last_notified_at"], row["reminder_minutes"], now)
        ]

    def mark_notified(self, task_id: int, notification_time: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET last_notified_for = ?, last_notified_at = ?
                WHERE id = ?
                """,
                (
                    notification_time.date().isoformat(),
                    notification_time.isoformat(timespec="seconds"),
                    task_id,
                ),
            )

    @staticmethod
    def _should_notify(
        last_notified_at: str | None, reminder_minutes: int, now: datetime
    ) -> bool:
        if reminder_minutes <= 0:
            return False
        if not last_notified_at:
            return True
        try:
            last_notification = datetime.fromisoformat(last_notified_at)
        except ValueError:
            return True
        return now - last_notification >= timedelta(minutes=reminder_minutes)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            user_id=row["user_id"],
            description=row["description"],
            due_date=row["due_date"],
            subject=row["subject"],
            priority=row["priority"],
            reminder_minutes=int(row["reminder_minutes"]),
            last_notified_at=row["last_notified_at"],
            is_done=bool(row["is_done"]),
        )
