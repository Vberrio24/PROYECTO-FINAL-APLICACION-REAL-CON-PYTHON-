from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

from .database import Task


SPANISH_MONTH_NAMES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]

WEEKDAY_NAMES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
WEEKDAY_SHORT = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]


def write_user_calendar_html(
    calendar_dir: str | Path, user_id: int, tasks: list[Task]
) -> Path:
    output_dir = Path(calendar_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    calendar_path = output_dir / f"calendario_{user_id}.html"
    calendar_path.write_text(build_html_calendar(tasks), encoding="utf-8")
    return calendar_path


def build_html_calendar(tasks: list[Task], today: date | None = None) -> str:
    today = today or date.today()
    scheduled_tasks = [task for task in tasks if task.due_date]
    tasks_by_day: dict[str, list[Task]] = defaultdict(list)
    for task in scheduled_tasks:
        tasks_by_day[task.due_date].append(task)

    months = list_calendar_months(scheduled_tasks, today)
    month_sections = "\n".join(
        render_month(month, tasks_by_day, today) for month in months
    )
    total_tasks = len(scheduled_tasks)
    subtitle = (
        f"{total_tasks} tarea programada"
        if total_tasks == 1
        else f"{total_tasks} tareas programadas"
    )

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calendario Academico</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #202124;
      --muted: #667085;
      --line: #d0d5dd;
      --header: #101828;
      --soft: #eef2f6;
      --today: #2563eb;
      --high: #c2410c;
      --medium: #a16207;
      --low: #047857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1180px, calc(100% - 24px));
      margin: 24px auto;
    }}
    .topbar {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    .subtitle {{
      margin: 0 0 20px;
      color: var(--muted);
      font-size: 14px;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
      font-size: 12px;
      color: var(--muted);
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 8px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 999px;
    }}
    .dot {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
    }}
    .dot.high {{ background: var(--high); }}
    .dot.medium {{ background: var(--medium); }}
    .dot.low {{ background: var(--low); }}
    .month {{
      margin-bottom: 28px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 10px 24px rgba(16, 24, 40, 0.08);
    }}
    .month-title {{
      margin: 0;
      padding: 14px 16px;
      color: #ffffff;
      background: var(--header);
      font-size: 20px;
      letter-spacing: 0;
    }}
    .weekdays,
    .grid {{
      display: grid;
      grid-template-columns: repeat(7, minmax(110px, 1fr));
      gap: 1px;
      background: var(--line);
    }}
    .weekdays div {{
      background: var(--soft);
      padding: 10px;
      font-size: 13px;
      font-weight: 700;
      text-align: center;
    }}
    .day {{
      min-height: 132px;
      background: var(--panel);
      padding: 8px;
    }}
    .day header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .weekday-mobile {{ display: none; }}
    .task-count {{
      font-size: 11px;
      color: var(--muted);
      font-weight: 400;
    }}
    .muted {{
      background: #f8fafc;
      color: var(--muted);
    }}
    .today {{
      outline: 2px solid var(--today);
      outline-offset: -2px;
    }}
    .task {{
      border-left: 4px solid var(--medium);
      background: #faf8f0;
      padding: 7px;
      margin: 6px 0;
      border-radius: 6px;
      font-size: 12px;
      color: var(--text);
    }}
    .task strong,
    .task span {{
      display: block;
      line-height: 1.3;
    }}
    .task span {{
      color: var(--muted);
      margin-top: 3px;
    }}
    .priority-alta {{ border-color: var(--high); background: #fff7ed; }}
    .priority-media {{ border-color: var(--medium); background: #fff8e1; }}
    .priority-baja {{ border-color: var(--low); background: #eefaf3; }}
    @media (max-width: 820px) {{
      .topbar {{ display: block; }}
      .legend {{ justify-content: flex-start; margin-bottom: 14px; }}
      .weekdays {{ display: none; }}
      .grid {{ display: block; border: 0; background: transparent; }}
      .day {{ min-height: auto; margin-bottom: 8px; border: 1px solid var(--line); }}
      .muted {{ display: none; }}
      .weekday-mobile {{ display: inline; color: var(--muted); font-weight: 400; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="topbar">
      <div>
        <h1>Calendario Academico</h1>
        <p class="subtitle">{escape(subtitle)}</p>
      </div>
      <div class="legend">
        <span><i class="dot high"></i>Alta</span>
        <span><i class="dot medium"></i>Media</span>
        <span><i class="dot low"></i>Baja</span>
      </div>
    </div>
    {month_sections}
  </main>
</body>
</html>
"""


def list_calendar_months(tasks: list[Task], today: date) -> list[date]:
    if not tasks:
        return [today.replace(day=1)]
    task_months = sorted(
        {date.fromisoformat(task.due_date).replace(day=1) for task in tasks if task.due_date}
    )
    months = []
    current = task_months[0]
    last = task_months[-1]
    while current <= last:
        months.append(current)
        current = next_month(current)
    return months


def render_month(
    month: date, tasks_by_day: dict[str, list[Task]], today: date
) -> str:
    weeks = build_month_grid(month)
    day_cells = []
    for week in weeks:
        for day in week:
            day_key = day.isoformat()
            day_tasks = tasks_by_day.get(day_key, [])
            classes = ["day"]
            if day.month != month.month:
                classes.append("muted")
            if day == today:
                classes.append("today")
            task_items = "\n".join(render_task_item(task) for task in day_tasks)
            task_count = "" if not day_tasks else f"<span class=\"task-count\">{len(day_tasks)}</span>"
            day_cells.append(
                f"""
                <section class="{' '.join(classes)}">
                    <header>
                      <span>{day.day} <small class="weekday-mobile">{WEEKDAY_SHORT[day.weekday()]}</small></span>
                      {task_count}
                    </header>
                    {task_items}
                </section>
                """.strip()
            )
    weekdays = "".join(f"<div>{name}</div>" for name in WEEKDAY_NAMES)
    return f"""
    <section class="month">
      <h2 class="month-title">{escape(format_month_title(month))}</h2>
      <div class="weekdays">{weekdays}</div>
      <div class="grid">{''.join(day_cells)}</div>
    </section>
    """.strip()


def render_task_item(task: Task) -> str:
    return f"""
    <div class="task {priority_class(task.priority)}">
        <strong>{escape(task.description)}</strong>
        <span>{escape(task.subject)} - {escape(task.priority)}</span>
    </div>
    """.strip()


def format_month_title(month: date) -> str:
    return f"{SPANISH_MONTH_NAMES[month.month - 1].capitalize()} {month.year}"


def next_month(month: date) -> date:
    if month.month == 12:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def build_month_grid(month: date) -> list[list[date]]:
    first_day = month.replace(day=1)
    start_day = first_day.toordinal() - first_day.weekday()
    return [
        [date.fromordinal(start_day + week * 7 + day) for day in range(7)]
        for week in range(6)
    ]


def priority_class(priority: str) -> str:
    normalized = priority.strip().lower()
    if normalized == "alta":
        return "priority-alta"
    if normalized == "baja":
        return "priority-baja"
    return "priority-media"
