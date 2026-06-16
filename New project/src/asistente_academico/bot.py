from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .calendar_export import write_user_calendar_html
from .config import load_settings
from .database import Task, TaskRepository


MENU_OPTIONS = {
    "1": "Agregar tarea",
    "2": "Mostrar tareas",
    "3": "Vencidas",
    "4": "Calendario",
    "5": "Estadisticas",
    "6": "Ayuda",
}

MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["1. Agregar tarea", "2. Mostrar tareas"],
        ["3. Vencidas", "4. Calendario"],
        ["5. Estadisticas", "6. Ayuda"],
    ],
    resize_keyboard=True,
)

PRIORITIES = {"alta": "Alta", "media": "Media", "baja": "Baja"}

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

HELP_TEXT = """
Opciones principales:

1. Agregar tarea
2. Mostrar tareas
3. Vencidas
4. Calendario
5. Estadisticas
6. Ayuda

Comandos:
/agregar <tarea> | <fecha> | <materia> | <prioridad> | <recordatorio>
/editar <id> | <tarea> | <fecha> | <materia> | <prioridad> | <recordatorio>
/tareas
/vencidas
/calendario
/hecho <id>
/borrar <id>
/stats
/ayuda

Ejemplos:
/agregar Preparar demo | manana | Programacion Avanzada | alta | cada 2 horas
/editar 3 | Preparar demo final | 16 de junio | Programacion Avanzada | alta | diario

Fechas aceptadas: 2026-06-15, 15-06-2026, 15/06/2026, hoy, manana,
pasado manana, en 3 dias o 16 de junio.

Recordatorios aceptados: sin recordatorio, cada 30 min, cada 2 horas o diario.
""".strip()


@dataclass(frozen=True)
class TaskInput:
    description: str
    due_date: str | None
    subject: str
    priority: str
    reminder_minutes: int
    invalid_due_date: str | None = None
    invalid_reminder: str | None = None


@dataclass(frozen=True)
class EditTaskInput:
    task_id: int | None
    task_input: TaskInput | None


def get_repository(context: ContextTypes.DEFAULT_TYPE) -> TaskRepository:
    return context.bot_data["repository"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name if update.effective_user else "estudiante"
    await update.message.reply_text(
        f"Hola, {name}. Soy Asistente Academico y te ayudo a organizar tus tareas.\n\n"
        "Usa el menu principal o escribe /ayuda.",
        reply_markup=MENU_KEYBOARD,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, reply_markup=MENU_KEYBOARD)


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Ejemplo: /agregar Entregar informe | manana | Redes | alta | diario",
            reply_markup=MENU_KEYBOARD,
        )
        return
    await save_task(update, context, parse_task_input(text))


async def edit_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    edit_input = parse_edit_task_input(" ".join(context.args).strip())
    if edit_input.task_id is None or edit_input.task_input is None:
        await update.message.reply_text(
            "Usa: /editar <id> | <tarea> | <fecha> | <materia> | <prioridad> | <recordatorio>",
            reply_markup=MENU_KEYBOARD,
        )
        return
    await update_task_from_input(update, context, edit_input.task_id, edit_input.task_input)


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    tasks = repository.list_pending(update.effective_user.id)
    if not tasks:
        await update.message.reply_text("No tienes tareas pendientes.", reply_markup=MENU_KEYBOARD)
        return
    await send_task_list(update, "Tus tareas pendientes:", tasks)


async def overdue_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    tasks = repository.list_overdue(update.effective_user.id, date.today())
    if not tasks:
        await update.message.reply_text(
            "Excelente: no tienes tareas vencidas.", reply_markup=MENU_KEYBOARD
        )
        return
    await send_task_list(update, "Tareas vencidas:", tasks)


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    tasks = repository.list_calendar(update.effective_user.id)
    if not tasks:
        await update.message.reply_text(
            "No tienes tareas programadas con fecha.", reply_markup=MENU_KEYBOARD
        )
        return

    calendar_path = refresh_user_calendar(context, update.effective_user.id)
    with calendar_path.open("rb") as calendar_file:
        await update.message.reply_document(
            document=calendar_file,
            filename=calendar_path.name,
            caption="Calendario visual listo. Abre el archivo HTML para verlo por dias y prioridades.",
            reply_markup=MENU_KEYBOARD,
        )


async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    task_id = parse_task_id(context.args)
    if task_id is None:
        await update.message.reply_text("Indica el id. Ejemplo: /hecho 3", reply_markup=MENU_KEYBOARD)
        return

    repository = get_repository(context)
    was_updated = repository.mark_done(update.effective_user.id, task_id)
    if was_updated:
        refresh_user_calendar(context, update.effective_user.id)
        await update.message.reply_text(f"Tarea #{task_id} completada.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text(
            "No encontre esa tarea pendiente. Revisa el id con /tareas.",
            reply_markup=MENU_KEYBOARD,
        )


async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    task_id = parse_task_id(context.args)
    if task_id is None:
        await update.message.reply_text("Indica el id. Ejemplo: /borrar 3", reply_markup=MENU_KEYBOARD)
        return

    repository = get_repository(context)
    was_deleted = repository.delete_task(update.effective_user.id, task_id)
    if was_deleted:
        refresh_user_calendar(context, update.effective_user.id)
        await update.message.reply_text(f"Tarea #{task_id} eliminada.", reply_markup=MENU_KEYBOARD)
    else:
        await update.message.reply_text("No encontre una tarea con ese id.", reply_markup=MENU_KEYBOARD)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    user_id = update.effective_user.id
    pending, done = repository.count_by_status(user_id)
    overdue = repository.count_overdue(user_id, date.today())
    subjects = repository.count_pending_by_subject(user_id)
    subject_lines = [f"- {subject}: {total}" for subject, total in subjects]
    subject_summary = "\n".join(subject_lines) if subject_lines else "- Sin pendientes"

    await update.message.reply_text(
        "Resumen academico:\n"
        f"Pendientes: {pending}\n"
        f"Completadas: {done}\n"
        f"Vencidas: {overdue}\n\n"
        f"Pendientes por materia:\n{subject_summary}",
        reply_markup=MENU_KEYBOARD,
    )


async def handle_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, task_id_text = query.data.split(":", maxsplit=1)
    task_id = parse_task_id([task_id_text])
    if task_id is None:
        await query.edit_message_text("No pude identificar esa tarea.")
        return

    repository = get_repository(context)
    user_id = query.from_user.id

    if action == "done":
        was_updated = repository.mark_done(user_id, task_id)
        if was_updated:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"Tarea #{task_id} completada." if was_updated else "No encontre esa tarea pendiente."
        )
        return

    if action == "delete":
        was_deleted = repository.delete_task(user_id, task_id)
        if was_deleted:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"Tarea #{task_id} eliminada." if was_deleted else "No encontre esa tarea."
        )
        return

    if action == "postpone":
        new_due_date = date.today() + timedelta(days=1)
        was_postponed = repository.postpone_task(user_id, task_id, new_due_date)
        if was_postponed:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"Tarea #{task_id} pospuesta para {new_due_date.isoformat()}."
            if was_postponed
            else "No encontre esa tarea pendiente."
        )
        return

    if action == "edit":
        task = repository.get_task(user_id, task_id)
        if not task or task.is_done:
            await query.edit_message_text("No encontre esa tarea pendiente.")
            return
        context.user_data["awaiting"] = "edit_task"
        context.user_data["edit_task_id"] = task_id
        await query.message.reply_text(
            "Envia los nuevos datos:\n\n"
            "Descripcion | fecha | materia | prioridad | recordatorio\n\n"
            f"Actual: {format_task_inline(task)}",
            reply_markup=MENU_KEYBOARD,
        )
        return

    await query.edit_message_text("Accion no reconocida.")


async def start_reminder_loop(application: Application) -> None:
    application.create_task(reminder_loop(application))


async def reminder_loop(application: Application) -> None:
    while True:
        await send_due_date_reminders(application)
        await asyncio.sleep(60)


async def send_due_date_reminders(application: Application) -> None:
    repository = application.bot_data["repository"]
    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    tasks = repository.list_tasks_for_reminder(today, tomorrow, now)

    for task in tasks:
        when = "hoy" if task.due_date == today.isoformat() else "manana"
        message = (
            f"Recordatorio: {task.description} vence {when}.\n"
            f"Materia: {task.subject}\n"
            f"Prioridad: {task.priority}\n"
            f"Frecuencia: {format_reminder_interval(task.reminder_minutes)}"
        )
        try:
            await application.bot.send_message(chat_id=task.user_id, text=message)
            repository.mark_notified(task.id, now)
        except Exception as error:
            logging.warning("No se pudo enviar recordatorio de tarea %s: %s", task.id, error)


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    action = normalize_menu_action(text)
    awaiting = context.user_data.get("awaiting")

    if awaiting == "task_description":
        context.user_data.pop("awaiting", None)
        await save_task(update, context, parse_task_input(text))
        return

    if awaiting == "edit_task":
        task_id = context.user_data.pop("edit_task_id", None)
        context.user_data.pop("awaiting", None)
        await update_task_from_input(update, context, task_id, parse_task_input(text))
        return

    if action == "1":
        context.user_data["awaiting"] = "task_description"
        await update.message.reply_text(
            "Escribe la tarea:\n\n"
            "Descripcion | fecha | materia | prioridad | recordatorio\n\n"
            "Ejemplo: Estudiar para sustentacion | manana | Programacion | alta | diario"
        )
        return
    if action == "2":
        await list_tasks(update, context)
        return
    if action == "3":
        await overdue_tasks(update, context)
        return
    if action == "4":
        await calendar_command(update, context)
        return
    if action == "5":
        await stats(update, context)
        return
    if action == "6":
        await help_command(update, context)
        return

    await update.message.reply_text(
        "No entendi tu mensaje. Usa el menu o escribe /ayuda.",
        reply_markup=MENU_KEYBOARD,
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("No conozco ese comando. Usa /ayuda.", reply_markup=MENU_KEYBOARD)


async def save_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    task_input: TaskInput,
) -> None:
    if not await validate_task_input(update, task_input):
        return

    repository = get_repository(context)
    task_id = repository.add_task(
        user_id=update.effective_user.id,
        description=task_input.description,
        due_date=task_input.due_date,
        subject=task_input.subject,
        priority=task_input.priority,
        reminder_minutes=task_input.reminder_minutes,
    )
    refresh_user_calendar(context, update.effective_user.id)

    await update.message.reply_text(
        f"Tarea #{task_id} guardada.\n"
        f"Materia: {task_input.subject}\n"
        f"Prioridad: {task_input.priority}\n"
        f"Fecha: {task_input.due_date or 'sin fecha'}\n"
        f"Recordatorio: {format_reminder_interval(task_input.reminder_minutes)}",
        reply_markup=MENU_KEYBOARD,
    )


async def update_task_from_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    task_id: int | None,
    task_input: TaskInput,
) -> None:
    if task_id is None:
        await update.message.reply_text("No pude identificar la tarea.", reply_markup=MENU_KEYBOARD)
        return
    if not await validate_task_input(update, task_input):
        return

    repository = get_repository(context)
    was_updated = repository.update_task(
        user_id=update.effective_user.id,
        task_id=task_id,
        description=task_input.description,
        due_date=task_input.due_date,
        subject=task_input.subject,
        priority=task_input.priority,
        reminder_minutes=task_input.reminder_minutes,
    )
    if not was_updated:
        await update.message.reply_text(
            "No encontre esa tarea pendiente para editar.", reply_markup=MENU_KEYBOARD
        )
        return
    refresh_user_calendar(context, update.effective_user.id)
    await update.message.reply_text(f"Tarea #{task_id} actualizada.", reply_markup=MENU_KEYBOARD)


async def validate_task_input(update: Update, task_input: TaskInput) -> bool:
    if len(task_input.description) < 3:
        await update.message.reply_text(
            "La descripcion debe tener al menos 3 caracteres.", reply_markup=MENU_KEYBOARD
        )
        return False
    if task_input.invalid_due_date:
        await update.message.reply_text(
            "No entendi la fecha. Usa 2026-06-15, 15/06/2026, manana, en 3 dias o 16 de junio.",
            reply_markup=MENU_KEYBOARD,
        )
        return False
    if task_input.invalid_reminder:
        await update.message.reply_text(
            "No entendi el recordatorio. Usa diario, cada 30 min, cada 2 horas o sin recordatorio.",
            reply_markup=MENU_KEYBOARD,
        )
        return False
    return True


async def send_task_list(update: Update, title: str, tasks: list[Task]) -> None:
    await update.message.reply_text(title, reply_markup=MENU_KEYBOARD)
    for task in tasks:
        await update.message.reply_text(format_task(task), reply_markup=task_action_keyboard(task.id))


def parse_task_input(text: str) -> TaskInput:
    parts = [part.strip() for part in text.split("|")]
    description = parts[0]
    due_date = normalize_due_date(parts[1]) if len(parts) >= 2 and parts[1] else None
    invalid_due_date = parts[1] if len(parts) >= 2 and parts[1] and not due_date else None
    subject = normalize_subject(parts[2]) if len(parts) >= 3 and parts[2] else "General"
    priority = normalize_priority(parts[3]) if len(parts) >= 4 and parts[3] else "Media"
    reminder_minutes = normalize_reminder_interval(parts[4]) if len(parts) >= 5 and parts[4] else 1440
    invalid_reminder = parts[4] if len(parts) >= 5 and parts[4] and reminder_minutes is None else None
    return TaskInput(
        description=description,
        due_date=due_date,
        subject=subject,
        priority=priority,
        reminder_minutes=reminder_minutes if reminder_minutes is not None else 1440,
        invalid_due_date=invalid_due_date,
        invalid_reminder=invalid_reminder,
    )


def parse_edit_task_input(text: str) -> EditTaskInput:
    parts = [part.strip() for part in text.split("|")]
    if not parts:
        return EditTaskInput(None, None)
    first_part = parts[0].split(maxsplit=1)
    task_id = parse_task_id(first_part[:1])
    if task_id is None:
        return EditTaskInput(None, None)
    task_text = " | ".join([first_part[1], *parts[1:]]) if len(first_part) > 1 else " | ".join(parts[1:])
    return EditTaskInput(task_id, parse_task_input(task_text))


def parse_task_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        task_id = int(args[0])
    except ValueError:
        return None
    return task_id if task_id > 0 else None


def normalize_menu_action(text: str) -> str | None:
    normalized = normalize_text(text)
    if normalized in MENU_OPTIONS:
        return normalized
    if normalized.startswith("1.") or "agregar" in normalized:
        return "1"
    if normalized.startswith("2.") or normalized == "tareas" or "mostrar tareas" in normalized:
        return "2"
    if normalized.startswith("3.") or "vencida" in normalized:
        return "3"
    if normalized.startswith("4.") or "calendario" in normalized or "agenda" in normalized:
        return "4"
    if normalized.startswith("5.") or "estadisticas" in normalized or "stats" in normalized:
        return "5"
    if normalized.startswith("6.") or "ayuda" in normalized:
        return "6"
    return None


def normalize_due_date(value: str, today: date | None = None) -> str | None:
    clean_value = value.strip()
    if not clean_value:
        return None
    today = today or date.today()
    normalized = normalize_text(clean_value)

    relative_dates = {
        "hoy": today,
        "manana": today + timedelta(days=1),
        "pasado manana": today + timedelta(days=2),
    }
    if normalized in relative_dates:
        return relative_dates[normalized].isoformat()

    days_match = re.fullmatch(r"en\s+(\d+)\s+dias?", normalized)
    if days_match:
        return (today + timedelta(days=int(days_match.group(1)))).isoformat()

    for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(clean_value, date_format).date().isoformat()
        except ValueError:
            continue

    month_match = re.fullmatch(r"(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?", normalized)
    if month_match:
        day = int(month_match.group(1))
        month = SPANISH_MONTHS.get(month_match.group(2))
        year = int(month_match.group(3)) if month_match.group(3) else today.year
        if month:
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if not month_match.group(3) and candidate < today:
                candidate = date(year + 1, month, day)
            return candidate.isoformat()
    return None


def normalize_subject(value: str) -> str:
    subject = " ".join(value.strip().split())
    return subject[:60] if subject else "General"


def normalize_priority(value: str) -> str:
    normalized = normalize_text(value)
    if normalized in {"urgente", "alta", "alto"}:
        return "Alta"
    if normalized in {"normal", "media", "medio"}:
        return "Media"
    if normalized in {"baja", "bajo"}:
        return "Baja"
    return PRIORITIES.get(normalized, "Media")


def normalize_reminder_interval(value: str) -> int | None:
    normalized = normalize_text(value)
    if normalized in {"sin", "no", "ninguno", "sin recordatorio", "desactivar"}:
        return 0
    if normalized in {"diario", "cada dia", "cada 1 dia", "1 dia"}:
        return 1440
    if normalized in {"hora", "cada hora", "cada 1 hora", "1 hora"}:
        return 60

    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(min|minuto|minutos)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)))
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(h|hora|horas)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)) * 60)
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(dia|dias)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)) * 1440)
    return None


def clamp_reminder_minutes(minutes: int) -> int:
    return max(5, min(minutes, 10080))


def format_reminder_interval(minutes: int) -> str:
    if minutes <= 0:
        return "sin recordatorio"
    if minutes == 1440:
        return "diario"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return f"cada {days} dia" if days == 1 else f"cada {days} dias"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"cada {hours} hora" if hours == 1 else f"cada {hours} horas"
    return f"cada {minutes} min"


def normalize_text(value: str) -> str:
    without_accents = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in without_accents if not unicodedata.combining(char))
    return " ".join(ascii_text.lower().strip().split())


def format_task(task: Task) -> str:
    return (
        f"#{task.id} {task.description}\n"
        f"Materia: {task.subject}\n"
        f"Prioridad: {task.priority}\n"
        f"Fecha: {task.due_date or 'Sin fecha'}\n"
        f"Recordatorio: {format_reminder_interval(task.reminder_minutes)}"
    )


def format_task_inline(task: Task) -> str:
    return (
        f"{task.description} | {task.due_date or 'sin fecha'} | {task.subject} | "
        f"{task.priority} | {format_reminder_interval(task.reminder_minutes)}"
    )


def task_action_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Completar", callback_data=f"done:{task_id}"),
                InlineKeyboardButton("Editar", callback_data=f"edit:{task_id}"),
            ],
            [
                InlineKeyboardButton("Posponer", callback_data=f"postpone:{task_id}"),
                InlineKeyboardButton("Eliminar", callback_data=f"delete:{task_id}"),
            ],
        ]
    )


def refresh_user_calendar(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    repository = get_repository(context)
    calendar_dir = context.bot_data["calendar_dir"]
    tasks = repository.list_scheduled(user_id, include_done=True)
    return write_user_calendar_html(calendar_dir, user_id, tasks)


def build_application(token: str, repository: TaskRepository, calendar_dir: str) -> Application:
    application = (
        Application.builder()
        .token(token)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .post_init(start_reminder_loop)
        .build()
    )
    application.bot_data["repository"] = repository
    application.bot_data["calendar_dir"] = calendar_dir

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("agregar", add_task))
    application.add_handler(CommandHandler("editar", edit_task))
    application.add_handler(CommandHandler("tareas", list_tasks))
    application.add_handler(CommandHandler("vencidas", overdue_tasks))
    application.add_handler(CommandHandler("calendario", calendar_command))
    application.add_handler(CommandHandler("hecho", mark_done))
    application.add_handler(CommandHandler("borrar", delete_task))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(handle_task_action))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    settings = load_settings()
    repository = TaskRepository(settings.database_path)

    while True:
        application = build_application(
            settings.telegram_bot_token,
            repository,
            settings.calendar_dir,
        )
        print("Asistente Academico encendido. Ve a Telegram y escribe /start.")
        print("Para apagar el bot, presiona Ctrl + C en esta terminal.")
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            break
        except (TimedOut, NetworkError) as error:
            logging.warning("Conexion con Telegram fallida: %s", error)
            print(
                "No se pudo conectar con Telegram. Revisa tu internet o VPN/firewall. "
                "Reintentando en 15 segundos..."
            )
            time.sleep(15)
