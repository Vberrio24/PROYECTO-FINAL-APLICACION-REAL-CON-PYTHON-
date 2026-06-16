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
📚 Bienvenido al Asistente Académico

Este bot te ayuda a gestionar tus deberes, exámenes y proyectos de forma inteligente.

Gestión de tareas
• Crear tarea: Elige la opción 1 del menú y sigue el asistente paso a paso.
• Ver tareas: Escribe /tareas o selecciona la opción 2 del menú.
• Completar: Usa el botón Completar en la tarea o escribe /hecho <número>.
• Eliminar: Usa el botón Eliminar en la tarea o escribe /borrar <número>.

Recordatorios
• Seguimiento automático: El bot te avisa según la frecuencia elegida.
• Lógica inteligente: Las notificaciones aumentan cuando el plazo está cerca.

Calendario
• Visualización académica: Genera un calendario interactivo en HTML con /calendario.

Dashboard
• Estadísticas: Consulta tu progreso e índice de productividad con /stats.

Menú
• Acceso rápido: Escribe "menu" en cualquier momento para ver las opciones.
""".strip()


def parse_12h_time(text: str) -> str | None:
    clean = text.strip().upper()
    match = re.fullmatch(r"(\d{1,2})[\s:.]*(\d{2})\s*(AM|PM)", clean)
    if not match:
        match_hour = re.fullmatch(r"(\d{1,2})\s*(AM|PM)", clean)
        if match_hour:
            hour = int(match_hour.group(1))
            am_pm = match_hour.group(2)
            minute = 0
        else:
            return None
    else:
        hour = int(match.group(1))
        minute = int(match.group(2))
        am_pm = match.group(3)

    if hour < 1 or hour > 12 or minute < 0 or minute > 59:
        return None

    if am_pm == "PM" and hour != 12:
        hour += 12
    elif am_pm == "AM" and hour == 12:
        hour = 0

    return f"{hour:02d}:{minute:02d}"


def format_24h_to_12h(time_str: str) -> str:
    if not time_str:
        return "11:59 PM"
    try:
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour == 0:
            h_12 = 12
            suffix = "AM"
        elif hour == 12:
            h_12 = 12
            suffix = "PM"
        elif hour > 12:
            h_12 = hour - 12
            suffix = "PM"
        else:
            h_12 = hour
            suffix = "AM"
        return f"{h_12}:{minute:02d} {suffix}"
    except Exception:
        return time_str


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
        context.user_data["create_flow"] = {}
        context.user_data["awaiting"] = "create_name"
        await update.message.reply_text("📚 *Vamos a crear una nueva tarea.*\n\n¿Cuál es el nombre de la actividad?", parse_mode="Markdown")
        return
    await save_task(update, context, parse_task_input(text))


async def edit_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args_text = " ".join(context.args).strip()
    if not args_text:
        await update.message.reply_text(
            "Usa: `/editar <numero>` (ej. `/editar 1`) para iniciar el asistente de edicion.",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        return

    # Intentar resolver el ID (puede ser el visual o el de la BD)
    first_arg = args_text.split("|")[0].strip()
    task_id = resolve_task_id(update.effective_user.id, first_arg, context)
    if task_id is None:
        await update.message.reply_text("❌ No pude identificar la tarea.", reply_markup=MENU_KEYBOARD)
        return

    # Si se usa el formato tradicional con barras
    if "|" in args_text:
        edit_input = parse_edit_task_input(args_text)
        if edit_input.task_input is None:
            await update.message.reply_text("❌ Formato incorrecto.", reply_markup=MENU_KEYBOARD)
            return
        await update_task_from_input(update, context, task_id, edit_input.task_input)
        return

    # Iniciar flujo guiado de edicion
    repository = get_repository(context)
    task = repository.get_task(update.effective_user.id, task_id)
    if not task:
        await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
        return

    context.user_data["edit_flow"] = {"task_id": task_id}
    context.user_data["awaiting"] = "edit_name"
    await update.message.reply_text(
        f"✏️ *Editar tarea #{task_id}*\n\n"
        f"¿Cual es el nuevo nombre de la actividad?\n"
        f"(escribe '.' para mantener: \"{task.description}\")",
        parse_mode="Markdown"
    )


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    tasks = repository.list_pending(update.effective_user.id)
    if not tasks:
        await update.message.reply_text("No tienes tareas pendientes.", reply_markup=MENU_KEYBOARD)
        return
        
    context.user_data["user_task_ids"] = [t.id for t in tasks]
    
    active_tasks_with_index = [(t, i) for i, t in enumerate(tasks, start=1) if t.status in ("Pendiente", "En progreso")]
    overdue_tasks_with_index = [(t, i) for i, t in enumerate(tasks, start=1) if t.status == "Vencida"]
    
    if active_tasks_with_index:
        await update.message.reply_text("📋 *Tus tareas activas:*", parse_mode="Markdown")
        for task, index in active_tasks_with_index:
            formatted = format_task_with_index(task, index)
            await update.message.reply_text(formatted, reply_markup=task_action_keyboard(task.id))
            
    if overdue_tasks_with_index:
        await update.message.reply_text("⚫ *Tareas vencidas (pendientes de entrega):*", parse_mode="Markdown")
        for task, index in overdue_tasks_with_index:
            formatted = format_task_with_index(task, index)
            await update.message.reply_text(formatted, reply_markup=task_action_keyboard(task.id))


async def overdue_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository = get_repository(context)
    tasks = repository.list_overdue(update.effective_user.id, date.today())
    if not tasks:
        await update.message.reply_text(
            "Excelente: no tienes tareas vencidas.", reply_markup=MENU_KEYBOARD
        )
        return
    context.user_data["user_task_ids"] = [t.id for t in tasks]
    await send_task_list(update, context, "Tareas vencidas:", tasks)


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
    input_text = context.args[0] if context.args else None
    if input_text is None:
        await update.message.reply_text("Indica el id. Ejemplo: /hecho 3", reply_markup=MENU_KEYBOARD)
        return

    task_id = resolve_task_id(update.effective_user.id, input_text, context)
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
    input_text = context.args[0] if context.args else None
    if input_text is None:
        await update.message.reply_text("Indica el id. Ejemplo: /borrar 3", reply_markup=MENU_KEYBOARD)
        return

    task_id = resolve_task_id(update.effective_user.id, input_text, context)
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
    
    # Refrescar estados y prioridades en la BD para este usuario
    repository._auto_update_overdue_tasks(user_id)
    repository._auto_update_priorities(user_id)
    
    tasks = repository.list_all_tasks_for_user(user_id)
    total_tasks = len(tasks)
    
    if not tasks:
        await update.message.reply_text(
            "📊 *DASHBOARD ACADÉMICO*\n\n"
            "Aun no tienes tareas registradas. ¡Usa /agregar o el menu para registrar tu primera actividad! 📚",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        return

    completed = sum(1 for t in tasks if t.is_done)
    in_progress = sum(1 for t in tasks if t.status == "En progreso")
    pending = sum(1 for t in tasks if t.status == "Pendiente")
    overdue = sum(1 for t in tasks if t.status == "Vencida")
    
    progress_pct = (completed / total_tasks * 100)
    
    on_time = sum(1 for t in tasks if t.is_done and (not t.due_date or (t.completed_at and t.completed_at[:10] <= t.due_date)))
    productivity_index = (on_time / completed * 100) if completed > 0 else 0.0

    # Próxima entrega (excluyendo vencidas y completadas)
    pending_scheduled = [t for t in tasks if t.status in ("Pendiente", "En progreso") and t.due_date]
    pending_scheduled.sort(key=lambda t: (t.due_date, t.due_time or "23:59"))
    next_delivery_text = "_No hay entregas programadas._"
    if pending_scheduled:
        next_t = pending_scheduled[0]
        next_delivery_text = f"🔹 *{next_t.description}* ({next_t.subject})\n📅 {next_t.due_date} a las {format_24h_to_12h(next_t.due_time)}"

    # Tarea más urgente
    pending_tasks = [t for t in tasks if not t.is_done]
    def sort_urgency(t: Task):
        if t.priority == "Crítica":
            p_val = 0
        elif t.priority == "Alta":
            p_val = 1
        elif t.priority == "Media":
            p_val = 2
        else:
            p_val = 3
        d_val = t.due_date or "9999-12-31"
        return (p_val, d_val, t.id)
    pending_tasks.sort(key=sort_urgency)
    
    urgent_task_text = "_No hay tareas pendientes._"
    if pending_tasks:
        urg = pending_tasks[0]
        urgent_task_text = f"🔥 *{urg.description}* ({urg.subject})\nPrioridad: {get_priority_emoji(urg.priority)} {urg.priority}"

    # Carga por materia
    subjects = repository.count_pending_by_subject(user_id)
    subject_lines = [f"• {subject}: *{total}*" for subject, total in subjects]
    subject_summary = "\n".join(subject_lines) if subject_lines else "• _Sin asignaturas pendientes_"

    dashboard_text = (
        f"📊 *DASHBOARD ACADÉMICO*\n"
        f"----------------------------------\n"
        f"📚 *Total de Tareas:* {total_tasks}\n"
        f"🟢 *Completadas:* {completed}\n"
        f"🟡 *En Progreso:* {in_progress}\n"
        f"🔴 *Pendientes:* {pending}\n"
        f"⚫ *Vencidas:* {overdue}\n\n"
        f"📈 *Progreso General:* {progress_pct:.1f}%\n"
        f"🏆 *Índice de Productividad:* {productivity_index:.1f}%\n"
        f"_(Tareas completadas a tiempo)_\n\n"
        f"📅 *Próxima Entrega:*\n{next_delivery_text}\n\n"
        f"🔥 *Tarea más Urgente:*\n{urgent_task_text}\n"
        f"----------------------------------\n"
        f"📚 *Pendientes por Materia:*\n{subject_summary}"
    )

    await update.message.reply_text(
        dashboard_text,
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )


async def handle_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, task_id_text = query.data.split(":", maxsplit=1)
    task_id = parse_task_id([task_id_text])
    
    repository = get_repository(context)
    user_id = query.from_user.id

    if action == "create_rem_freq":
        minutes = int(task_id_text)
        await complete_task_creation_callback(query, context, minutes)
        return

    if action == "edit_rem_freq":
        minutes = int(task_id_text)
        await complete_task_editing_callback(query, context, minutes)
        return

    if task_id is None:
        await query.edit_message_text("No pude identificar esa tarea.")
        return

    if action == "done":
        task = repository.get_task(user_id, task_id)
        task_desc = task.description if task else "Tarea"
        was_updated = repository.mark_done(user_id, task_id)
        if was_updated:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"✅ Tarea *\"{task_desc}\"* completada." if was_updated else "No encontre esa tarea pendiente."
        )
        return

    if action == "delete":
        task = repository.get_task(user_id, task_id)
        task_desc = task.description if task else "Tarea"
        was_deleted = repository.delete_task(user_id, task_id)
        if was_deleted:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"❌ Tarea *\"{task_desc}\"* eliminada." if was_deleted else "No encontre esa tarea."
        )
        return

    if action == "postpone":
        task = repository.get_task(user_id, task_id)
        if not task or task.is_done:
            await query.edit_message_text("❌ No encontré esa tarea pendiente.")
            return
        context.user_data["awaiting"] = "postpone_date"
        context.user_data["postpone_task_id"] = task_id
        await query.message.reply_text(
            f"📅 *¿Para qué fecha deseas mover la tarea \"{task.description}\"?*\n\n"
            "Puedes usar formatos como:\n"
            "• `25-06-2026` o `25/06/2026`\n"
            "• `25 06 2026` o `25.06.2026`\n"
            "• `mañana` o `en 3 días`",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
        )
        await query.delete_message()
        return

    if action == "edit":
        task = repository.get_task(user_id, task_id)
        if not task or task.is_done:
            await query.edit_message_text("No encontre esa tarea pendiente.")
            return
        context.user_data["edit_flow"] = {"task_id": task_id}
        context.user_data["awaiting"] = "edit_name"
        await query.message.reply_text(
            f"✏️ *Editar tarea: \"{task.description}\"*\n\n"
            f"¿Cuál es el nuevo nombre de la actividad?\n"
            f"(Escribe `.` para mantener: \"{task.description}\")",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
        )
        await query.delete_message()
        return

    if action == "detail":
        task = repository.get_task(user_id, task_id)
        if not task:
            await query.message.reply_text("No encontre esa tarea.", reply_markup=MENU_KEYBOARD)
            return

        res_text = task.resources if task.resources else "Ninguno"
        if task.resources:
            links = [l.strip() for l in task.resources.split(",")]
            formatted_links = []
            for link in links:
                if link.startswith("http"):
                    formatted_links.append(f"[Enlace]({link})")
                else:
                    formatted_links.append(link)
            res_text = ", ".join(formatted_links)

        detail_msg = (
            f"📋 *DETALLES DE LA TAREA*\n"
            f"----------------------------------\n"
            f"📋 *Actividad:* {task.description}\n"
            f"📚 *Materia:* {task.subject}\n"
            f"📅 *Fecha límite:* {task.due_date or 'Sin fecha'} (a las {task.due_time})\n"
            f"🔥 *Prioridad:* {get_priority_emoji(task.priority)} {task.priority}\n"
            f"📊 *Estado:* {get_status_emoji(task.status)} {task.status}\n"
            f"📈 *Progreso:* {task.progress_pct}%\n\n"
            f"📝 *Anotaciones:* \n{task.notes or '_Sin anotaciones registradas._'}\n\n"
            f"🔗 *Recursos:* {res_text}\n"
            f"----------------------------------"
        )
        await query.message.reply_text(detail_msg, parse_mode="Markdown", reply_markup=MENU_KEYBOARD)
        return

    if action == "notes":
        task = repository.get_task(user_id, task_id)
        if not task:
            await query.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
            return
        context.user_data["notes_flow"] = {"task_id": task_id}
        context.user_data["awaiting"] = "notes_text"
        await query.message.reply_text(
            f"📝 *Agregar Anotaciones a la tarea: \"{task.description}\"*\n\n"
            f"Escribe los apuntes o notas de estudio que desees registrar.\n"
            f"(Escribe `.` para mantener el actual: \"{task.notes or 'Ninguno'}\" o escribe `borrar` para eliminarlos)",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD,
        )
        await query.delete_message()
        return

    if action == "rem_done":
        task = repository.get_task(user_id, task_id)
        task_desc = task.description if task else "Tarea"
        was_updated = repository.update_task_status(user_id, task_id, "Completada")
        if was_updated:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"🎉 ¡Excelente trabajo! Tarea *\"{task_desc}\"* marcada como completada. Se han desactivado los futuros recordatorios. ¡Sigue así! 🚀"
        )
        return

    if action == "rem_prog":
        task = repository.get_task(user_id, task_id)
        task_desc = task.description if task else "Tarea"
        was_updated = repository.update_task_status(user_id, task_id, "En progreso")
        if was_updated:
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"🟡 Estado de *\"{task_desc}\"* actualizado: \"En progreso\" (50% de avance). ¡Mucho éxito! 💪"
        )
        return

    if action == "rem_pending":
        task = repository.get_task(user_id, task_id)
        task_desc = task.description if task else "Tarea"
        if task:
            with repository._connect() as conn:
                conn.execute("UPDATE tasks SET priority = 'Alta', status = 'Pendiente', progress_pct = 0 WHERE id = ?", (task_id,))
            refresh_user_calendar(context, user_id)
        await query.edit_message_text(
            f"🔴 Estado de *\"{task_desc}\"*: \"Pendiente\" (0% de avance). He establecido la prioridad a Alta 🚨 para ayudarte a enfocar en ella. ¡A por ello!"
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

    # Refrescar estados y prioridades en la BD para todos los usuarios
    repository._auto_update_overdue_tasks_all()
    repository._auto_update_priorities_all()

    tasks = repository.list_all_pending()
    for task in tasks:
        if not task.due_date:
            continue

        if not should_notify_intelligent(task, now):
            continue

        try:
            due_time_str = task.due_time or "23:59"
            due_dt = datetime.fromisoformat(f"{task.due_date}T{due_time_str}")
            time_diff = due_dt - now
            hours_diff = time_diff.total_seconds() / 3600.0

            if 0 < hours_diff <= 1.0:
                message_text = (
                    f"🚨 *¡ÚLTIMO AVISO! Queda menos de 1 hora*\n\n"
                    f"La actividad *{task.description}* ({task.subject}) vence pronto a las {due_time_str}.\n"
                    f"Prioridad: {get_priority_emoji(task.priority)} {task.priority}\n"
                    f"Estado actual: {get_status_emoji(task.status)} {task.status}\n\n"
                    f"¿Ya la entregaste?"
                )
            elif 1.0 < hours_diff <= 6.0:
                message_text = (
                    f"⚡ *¡ALERTA CRÍTICA! Quedan {int(hours_diff)} horas*\n\n"
                    f"La actividad *{task.description}* ({task.subject}) debe entregarse hoy a las {due_time_str}.\n"
                    f"Prioridad: {get_priority_emoji(task.priority)} {task.priority}\n"
                    f"Estado actual: {get_status_emoji(task.status)} {task.status}\n\n"
                    f"¿Ya lograste finalizarla?"
                )
            elif 6.0 < hours_diff <= 24.0:
                message_text = (
                    f"🔔 *¡Recordatorio! Quedan {int(hours_diff)} horas*\n\n"
                    f"La actividad *{task.description}* ({task.subject}) vence a las {due_time_str}.\n"
                    f"Prioridad: {get_priority_emoji(task.priority)} {task.priority}\n"
                    f"Estado actual: {get_status_emoji(task.status)} {task.status}\n\n"
                    f"¿Ya está lista para entregar?"
                )
            else:
                message_text = (
                    f"📅 *Recordatorio Académico (Faltan {int(hours_diff // 24) + 1} días)*\n\n"
                    f"La actividad *{task.description}* ({task.subject}) vence el {task.due_date} a las {due_time_str}.\n"
                    f"Prioridad: {get_priority_emoji(task.priority)} {task.priority}\n"
                    f"Estado actual: {get_status_emoji(task.status)} {task.status}\n\n"
                    f"¿Cómo vas con esta entrega?"
                )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Sí, ya la entregué", callback_data=f"rem_done:{task.id}"),
                    ],
                    [
                        InlineKeyboardButton("🟡 La estoy realizando", callback_data=f"rem_prog:{task.id}"),
                    ],
                    [
                        InlineKeyboardButton("🔴 Aún no la he comenzado", callback_data=f"rem_pending:{task.id}"),
                    ],
                ]
            )
            try:
                await application.bot.send_message(
                    chat_id=task.user_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                repository.mark_notified(task.id, now)
            except Exception as error:
                logging.warning("No se pudo enviar recordatorio de tarea %s: %s", task.id, error)
        except Exception as err:
            logging.error("Error procesando recordatorio de tarea %s: %s", task.id, err)


async def complete_task_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, reminder_min: int) -> None:
    flow = context.user_data.pop("create_flow", {})
    context.user_data.pop("awaiting", None)
    
    description = flow.get("description", "Nueva Tarea")
    subject = flow.get("subject", "General")
    notes = flow.get("notes")
    due_date = flow.get("due_date")
    due_time = flow.get("due_time")
    
    # Recalculate default priority based on due date and time
    priority = "Baja"
    if due_date:
        now = datetime.now()
        try:
            due_dt = datetime.fromisoformat(f"{due_date}T{due_time or '23:59'}")
            time_diff = due_dt - now
            days_diff = time_diff.total_seconds() / 86400.0
            if days_diff <= 1.0:
                priority = "Crítica"
            elif days_diff <= 3.0:
                priority = "Alta"
            elif days_diff <= 7.0:
                priority = "Media"
        except Exception:
            pass
            
    repository = get_repository(context)
    task_id = repository.add_task(
        user_id=update.effective_user.id,
        description=description,
        due_date=due_date,
        subject=subject,
        priority=priority,
        reminder_minutes=reminder_min,
        due_time=due_time or "23:59",
        notes=notes
    )
    refresh_user_calendar(context, update.effective_user.id)
    
    # Fetch task to get its recalculated fields
    task = repository.get_task(update.effective_user.id, task_id)
    
    due_date_str = task.due_date or "Sin fecha"
    if task.due_date and task.due_time:
        due_date_str += f" a las {format_24h_to_12h(task.due_time)}"
        
    await update.message.reply_text(
        f"✅ *Tarea registrada correctamente.*\n\n"
        f"📋 *Actividad:* {task.description}\n"
        f"📖 *Materia:* {task.subject}\n"
        f"📝 *Descripción:* {task.notes or 'Ninguna'}\n"
        f"📅 *Fecha:* {due_date_str}\n"
        f"🔥 *Prioridad:* {get_priority_emoji(task.priority)} {task.priority}\n"
        f"⏰ *Recordatorio:* {format_reminder_interval(task.reminder_minutes)}",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD
    )


async def complete_task_creation_callback(query, context: ContextTypes.DEFAULT_TYPE, reminder_min: int) -> None:
    flow = context.user_data.pop("create_flow", {})
    context.user_data.pop("awaiting", None)
    
    description = flow.get("description", "Nueva Tarea")
    subject = flow.get("subject", "General")
    notes = flow.get("notes")
    due_date = flow.get("due_date")
    due_time = flow.get("due_time")
    
    # Recalculate default priority based on due date and time
    priority = "Baja"
    if due_date:
        now = datetime.now()
        try:
            due_dt = datetime.fromisoformat(f"{due_date}T{due_time or '23:59'}")
            time_diff = due_dt - now
            days_diff = time_diff.total_seconds() / 86400.0
            if days_diff <= 1.0:
                priority = "Crítica"
            elif days_diff <= 3.0:
                priority = "Alta"
            elif days_diff <= 7.0:
                priority = "Media"
        except Exception:
            pass
            
    repository = get_repository(context)
    task_id = repository.add_task(
        user_id=query.from_user.id,
        description=description,
        due_date=due_date,
        subject=subject,
        priority=priority,
        reminder_minutes=reminder_min,
        due_time=due_time or "23:59",
        notes=notes
    )
    refresh_user_calendar(context, query.from_user.id)
    
    # Fetch task to get recalculated fields
    task = repository.get_task(query.from_user.id, task_id)
    
    due_date_str = task.due_date or "Sin fecha"
    if task.due_date and task.due_time:
        due_date_str += f" a las {format_24h_to_12h(task.due_time)}"
        
    await query.edit_message_text(
        f"✅ *Tarea registrada correctamente.*\n\n"
        f"📋 *Actividad:* {task.description}\n"
        f"📖 *Materia:* {task.subject}\n"
        f"📝 *Descripción:* {task.notes or 'Ninguna'}\n"
        f"📅 *Fecha:* {due_date_str}\n"
        f"🔥 *Prioridad:* {get_priority_emoji(task.priority)} {task.priority}\n"
        f"⏰ *Recordatorio:* {format_reminder_interval(task.reminder_minutes)}",
        parse_mode="Markdown"
    )


async def complete_task_editing(update: Update, context: ContextTypes.DEFAULT_TYPE, reminder_min: int) -> None:
    flow = context.user_data.pop("edit_flow", {})
    context.user_data.pop("awaiting", None)
    
    task_id = flow.get("task_id")
    description = flow.get("description")
    subject = flow.get("subject")
    notes = flow.get("notes")
    due_date = flow.get("due_date")
    due_time = flow.get("due_time")
    
    # Recalculate priority
    priority = "Baja"
    if due_date:
        now = datetime.now()
        try:
            due_dt = datetime.fromisoformat(f"{due_date}T{due_time or '23:59'}")
            time_diff = due_dt - now
            days_diff = time_diff.total_seconds() / 86400.0
            if days_diff <= 1.0:
                priority = "Crítica"
            elif days_diff <= 3.0:
                priority = "Alta"
            elif days_diff <= 7.0:
                priority = "Media"
        except Exception:
            pass

    repository = get_repository(context)
    was_updated = repository.update_task(
        user_id=update.effective_user.id,
        task_id=task_id,
        description=description,
        due_date=due_date,
        subject=subject,
        priority=priority,
        reminder_minutes=reminder_min,
        due_time=due_time or "23:59",
        notes=notes
    )
    
    if was_updated:
        repository._auto_update_priorities(update.effective_user.id)
        refresh_user_calendar(context, update.effective_user.id)
        task = repository.get_task(update.effective_user.id, task_id)
        
        due_date_str = task.due_date or "Sin fecha"
        if task.due_date and task.due_time:
            due_date_str += f" a las {format_24h_to_12h(task.due_time)}"
            
        await update.message.reply_text(
            f"✅ *Tarea actualizada correctamente.*\n\n"
            f"📋 *Actividad:* {task.description}\n"
            f"📖 *Materia:* {task.subject}\n"
            f"📝 *Descripción:* {task.notes or 'Ninguna'}\n"
            f"📅 *Fecha:* {due_date_str}\n"
            f"🔥 *Prioridad:* {get_priority_emoji(task.priority)} {task.priority}\n"
            f"⏰ *Recordatorio:* {format_reminder_interval(task.reminder_minutes)}",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
        )
    else:
        await update.message.reply_text("❌ No se pudo actualizar la tarea.", reply_markup=MENU_KEYBOARD)


async def complete_task_editing_callback(query, context: ContextTypes.DEFAULT_TYPE, reminder_min: int) -> None:
    flow = context.user_data.pop("edit_flow", {})
    context.user_data.pop("awaiting", None)
    
    task_id = flow.get("task_id")
    description = flow.get("description")
    subject = flow.get("subject")
    notes = flow.get("notes")
    due_date = flow.get("due_date")
    due_time = flow.get("due_time")
    
    # Recalculate priority
    priority = "Baja"
    if due_date:
        now = datetime.now()
        try:
            due_dt = datetime.fromisoformat(f"{due_date}T{due_time or '23:59'}")
            time_diff = due_dt - now
            days_diff = time_diff.total_seconds() / 86400.0
            if days_diff <= 1.0:
                priority = "Crítica"
            elif days_diff <= 3.0:
                priority = "Alta"
            elif days_diff <= 7.0:
                priority = "Media"
        except Exception:
            pass

    repository = get_repository(context)
    was_updated = repository.update_task(
        user_id=query.from_user.id,
        task_id=task_id,
        description=description,
        due_date=due_date,
        subject=subject,
        priority=priority,
        reminder_minutes=reminder_min,
        due_time=due_time or "23:59",
        notes=notes
    )
    
    if was_updated:
        repository._auto_update_priorities(query.from_user.id)
        refresh_user_calendar(context, query.from_user.id)
        task = repository.get_task(query.from_user.id, task_id)
        
        due_date_str = task.due_date or "Sin fecha"
        if task.due_date and task.due_time:
            due_date_str += f" a las {format_24h_to_12h(task.due_time)}"
            
        await query.edit_message_text(
            f"✅ *Tarea actualizada correctamente.*\n\n"
            f"📋 *Actividad:* {task.description}\n"
            f"📖 *Materia:* {task.subject}\n"
            f"📝 *Descripción:* {task.notes or 'Ninguna'}\n"
            f"📅 *Fecha:* {due_date_str}\n"
            f"🔥 *Prioridad:* {get_priority_emoji(task.priority)} {task.priority}\n"
            f"⏰ *Recordatorio:* {format_reminder_interval(task.reminder_minutes)}",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("❌ No se pudo actualizar la tarea.")


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    action = normalize_menu_action(text)
    awaiting = context.user_data.get("awaiting")

    # If they pressed a main menu button, we cancel any ongoing conversational flow
    if action is not None:
        context.user_data.pop("awaiting", None)
        context.user_data.pop("create_flow", None)
        context.user_data.pop("edit_flow", None)
        context.user_data.pop("notes_flow", None)
        context.user_data.pop("postpone_task_id", None)
        awaiting = None

    if awaiting:
        if awaiting == "create_name":
            context.user_data["create_flow"] = {"description": text}
            context.user_data["awaiting"] = "create_subject"
            await update.message.reply_text(
                "📖 *¿A qué materia pertenece?*",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "create_subject":
            context.user_data["create_flow"]["subject"] = text
            context.user_data["awaiting"] = "create_notes"
            await update.message.reply_text(
                "📝 *Describe brevemente la actividad.*",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "create_notes":
            context.user_data["create_flow"]["notes"] = text
            context.user_data["awaiting"] = "create_due_date"
            await update.message.reply_text(
                "📅 *¿Cuál es la fecha de entrega?*\n\n"
                "Puedes usar formatos como: `20-06-2026`, `20/06/2026`, `mañana`, `en 3 días` o escribe `sin fecha`.",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "create_due_date":
            normalized = None
            if text.lower() not in {"sin fecha", "ninguna", "no", "sin"}:
                normalized = normalize_due_date(text)
                if not normalized:
                    await update.message.reply_text(
                        "❌ *Fecha inválida.*\n\n"
                        "Por favor, ingresa una fecha correcta (ej. `25-06-2026`, `25/06/2026`, `mañana`) o escribe `sin fecha`:",
                        parse_mode="Markdown",
                        reply_markup=MENU_KEYBOARD
                    )
                    return
            
            context.user_data["create_flow"]["due_date"] = normalized
            
            if normalized is None:
                context.user_data["create_flow"]["due_time"] = None
                context.user_data["awaiting"] = "create_reminder"
                
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("⏰ Cada 10 min", callback_data="create_rem_freq:10"),
                            InlineKeyboardButton("⏰ Cada 30 min", callback_data="create_rem_freq:30"),
                        ],
                        [
                            InlineKeyboardButton("⏰ Cada hora", callback_data="create_rem_freq:60"),
                            InlineKeyboardButton("⏰ Cada 2 horas", callback_data="create_rem_freq:120"),
                        ],
                        [
                            InlineKeyboardButton("⏰ Cada 6 horas", callback_data="create_rem_freq:360"),
                            InlineKeyboardButton("⏰ Cada 12 horas", callback_data="create_rem_freq:720"),
                        ],
                        [
                            InlineKeyboardButton("📅 Cada día", callback_data="create_rem_freq:1440"),
                            InlineKeyboardButton("📅 Cada 2 días", callback_data="create_rem_freq:2880"),
                        ],
                        [
                            InlineKeyboardButton("📅 Cada semana", callback_data="create_rem_freq:10080"),
                            InlineKeyboardButton("📅 Cada 2 semanas", callback_data="create_rem_freq:20160"),
                        ],
                        [
                            InlineKeyboardButton("🚫 Sin recordatorios", callback_data="create_rem_freq:0"),
                        ]
                    ]
                )
                await update.message.reply_text(
                    "⏰ *¿Cada cuánto deseas recibir recordatorios?*\n\n"
                    "Elige una opción o escribe otra frecuencia (ej. `cada 3 horas`, `diario`, `sin recordatorio`):",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                context.user_data["awaiting"] = "create_due_time"
                await update.message.reply_text(
                    "🕒 *¿A qué hora es la entrega?*\n\n"
                    "Ingresa la hora en formato de 12 horas (ej. `8:30 AM`, `1:30 PM`, `11:59 PM`, `8 PM`):",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
            return

        elif awaiting == "create_due_time":
            time_parsed = parse_12h_time(text)
            if not time_parsed:
                await update.message.reply_text(
                    "❌ *Hora inválida.*\n\n"
                    "Por favor, ingresa una hora correcta en formato de 12 horas (ej. `8:30 AM`, `1:30 PM`, `11:59 PM`, `8 PM`):",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
                return
            
            context.user_data["create_flow"]["due_time"] = time_parsed
            context.user_data["awaiting"] = "create_reminder"
            
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⏰ Cada 10 min", callback_data="create_rem_freq:10"),
                        InlineKeyboardButton("⏰ Cada 30 min", callback_data="create_rem_freq:30"),
                    ],
                    [
                        InlineKeyboardButton("⏰ Cada hora", callback_data="create_rem_freq:60"),
                        InlineKeyboardButton("⏰ Cada 2 horas", callback_data="create_rem_freq:120"),
                    ],
                    [
                        InlineKeyboardButton("⏰ Cada 6 horas", callback_data="create_rem_freq:360"),
                        InlineKeyboardButton("⏰ Cada 12 horas", callback_data="create_rem_freq:720"),
                    ],
                    [
                        InlineKeyboardButton("📅 Cada día", callback_data="create_rem_freq:1440"),
                        InlineKeyboardButton("📅 Cada 2 días", callback_data="create_rem_freq:2880"),
                    ],
                    [
                        InlineKeyboardButton("📅 Cada semana", callback_data="create_rem_freq:10080"),
                        InlineKeyboardButton("📅 Cada 2 semanas", callback_data="create_rem_freq:20160"),
                    ],
                    [
                        InlineKeyboardButton("🚫 Sin recordatorios", callback_data="create_rem_freq:0"),
                    ]
                ]
            )
            await update.message.reply_text(
                "⏰ *¿Cada cuánto deseas recibir recordatorios?*\n\n"
                "Elige una opción o escribe otra frecuencia (ej. `cada 3 horas`, `diario`, `sin recordatorio`):",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        elif awaiting == "create_reminder":
            reminder_min = normalize_reminder_interval(text)
            if reminder_min is None:
                await update.message.reply_text(
                    "❌ *Frecuencia no reconocida.*\n\n"
                    "Por favor, escribe un intervalo válido (ej. `cada 2 horas`, `diario`, `sin recordatorio`) o elige un botón:",
                    parse_mode="Markdown"
                )
                return
            await complete_task_creation(update, context, reminder_min)
            return

        elif awaiting == "edit_name":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return
            
            if text != ".":
                context.user_data["edit_flow"]["description"] = text
            else:
                context.user_data["edit_flow"]["description"] = task.description
            
            context.user_data["awaiting"] = "edit_subject"
            await update.message.reply_text(
                f"📖 *¿A qué materia pertenece?*\n"
                f"(escribe '.' para mantener: \"{task.subject}\")",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "edit_subject":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text != ".":
                context.user_data["edit_flow"]["subject"] = text
            else:
                context.user_data["edit_flow"]["subject"] = task.subject
            
            context.user_data["awaiting"] = "edit_notes"
            await update.message.reply_text(
                f"📝 *Describe brevemente la actividad.*\n"
                f"(escribe '.' para mantener: \"{task.notes or 'Ninguna'}\")",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "edit_notes":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text != ".":
                context.user_data["edit_flow"]["notes"] = text if text.lower() not in {"ninguna", "sin notas", "borrar"} else None
            else:
                context.user_data["edit_flow"]["notes"] = task.notes
            
            context.user_data["awaiting"] = "edit_due_date"
            await update.message.reply_text(
                f"📅 *¿Cuál es la fecha de entrega?*\n"
                f"(escribe '.' para mantener la actual: \"{task.due_date or 'Sin fecha'}\")\n\n"
                "O escribe `sin fecha` para quitarla.",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "edit_due_date":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text == ".":
                normalized = task.due_date
            elif text.lower() in {"sin fecha", "ninguna", "no", "sin"}:
                normalized = None
            else:
                normalized = normalize_due_date(text)
                if not normalized:
                    await update.message.reply_text(
                        "❌ *Fecha inválida.*\n\n"
                        "Por favor, ingresa una fecha correcta (ej. `25-06-2026`, `25/06/2026`, `mañana`) o escribe `.` para mantener:",
                        parse_mode="Markdown",
                        reply_markup=MENU_KEYBOARD
                    )
                    return
            
            context.user_data["edit_flow"]["due_date"] = normalized

            if normalized is None:
                context.user_data["edit_flow"]["due_time"] = None
                context.user_data["awaiting"] = "edit_reminder"
                
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("⏰ Cada 10 min", callback_data="edit_rem_freq:10"),
                            InlineKeyboardButton("⏰ Cada 30 min", callback_data="edit_rem_freq:30"),
                        ],
                        [
                            InlineKeyboardButton("⏰ Cada hora", callback_data="edit_rem_freq:60"),
                            InlineKeyboardButton("⏰ Cada 2 horas", callback_data="edit_rem_freq:120"),
                        ],
                        [
                            InlineKeyboardButton("⏰ Cada 6 horas", callback_data="edit_rem_freq:360"),
                            InlineKeyboardButton("⏰ Cada 12 horas", callback_data="edit_rem_freq:720"),
                        ],
                        [
                            InlineKeyboardButton("📅 Cada día", callback_data="edit_rem_freq:1440"),
                            InlineKeyboardButton("📅 Cada 2 días", callback_data="edit_rem_freq:2880"),
                        ],
                        [
                            InlineKeyboardButton("📅 Cada semana", callback_data="edit_rem_freq:10080"),
                            InlineKeyboardButton("📅 Cada 2 semanas", callback_data="edit_rem_freq:20160"),
                        ],
                        [
                            InlineKeyboardButton("🚫 Sin recordatorios", callback_data="edit_rem_freq:0"),
                        ]
                    ]
                )
                await update.message.reply_text(
                    f"⏰ *¿Cada cuánto deseas recibir recordatorios?*\n"
                    f"(escribe '.' para mantener: \"{format_reminder_interval(task.reminder_minutes)}\"):",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                context.user_data["awaiting"] = "edit_due_time"
                current_time_str = format_24h_to_12h(task.due_time) if task.due_time else "11:59 PM"
                await update.message.reply_text(
                    f"🕒 *¿A qué hora es la entrega?*\n"
                    f"(escribe '.' para mantener la actual: \"{current_time_str}\")\n\n"
                    "Ingresa la hora en formato de 12 horas (ej. `8:30 AM`, `1:30 PM`, `11:59 PM`, `8 PM`):",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
            return

        elif awaiting == "edit_due_time":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text == ".":
                time_parsed = task.due_time or "23:59"
            else:
                time_parsed = parse_12h_time(text)
                if not time_parsed:
                    await update.message.reply_text(
                        "❌ *Hora inválida.*\n\n"
                        "Por favor, ingresa una hora correcta en formato de 12 horas (ej. `8:30 AM`, `1:30 PM`, `11:59 PM`, `8 PM`) o escribe `.` para mantener:",
                        parse_mode="Markdown",
                        reply_markup=MENU_KEYBOARD
                    )
                    return
            
            context.user_data["edit_flow"]["due_time"] = time_parsed
            context.user_data["awaiting"] = "edit_reminder"
            
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⏰ Cada 10 min", callback_data="edit_rem_freq:10"),
                        InlineKeyboardButton("⏰ Cada 30 min", callback_data="edit_rem_freq:30"),
                    ],
                    [
                        InlineKeyboardButton("⏰ Cada hora", callback_data="edit_rem_freq:60"),
                        InlineKeyboardButton("⏰ Cada 2 horas", callback_data="edit_rem_freq:120"),
                    ],
                    [
                        InlineKeyboardButton("⏰ Cada 6 horas", callback_data="edit_rem_freq:360"),
                        InlineKeyboardButton("⏰ Cada 12 horas", callback_data="edit_rem_freq:720"),
                    ],
                    [
                        InlineKeyboardButton("📅 Cada día", callback_data="edit_rem_freq:1440"),
                        InlineKeyboardButton("📅 Cada 2 días", callback_data="edit_rem_freq:2880"),
                    ],
                    [
                        InlineKeyboardButton("📅 Cada semana", callback_data="edit_rem_freq:10080"),
                        InlineKeyboardButton("📅 Cada 2 semanas", callback_data="edit_rem_freq:20160"),
                    ],
                    [
                        InlineKeyboardButton("🚫 Sin recordatorios", callback_data="edit_rem_freq:0"),
                    ]
                ]
            )
            await update.message.reply_text(
                f"⏰ *¿Cada cuánto deseas recibir recordatorios?*\n"
                f"(escribe '.' para mantener: \"{format_reminder_interval(task.reminder_minutes)}\"):",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            return

        elif awaiting == "edit_reminder":
            repository = get_repository(context)
            task_id = context.user_data["edit_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("edit_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text == ".":
                reminder_min = task.reminder_minutes
            else:
                reminder_min = normalize_reminder_interval(text)
                if reminder_min is None:
                    await update.message.reply_text(
                        "❌ *Frecuencia no reconocida.*\n\n"
                        "Por favor, escribe un intervalo válido (ej. `cada 2 horas`, `diario`, `sin recordatorio`) o escribe `.` para mantener:",
                        parse_mode="Markdown"
                    )
                    return

            await complete_task_editing(update, context, reminder_min)
            return

        elif awaiting == "notes_text":
            repository = get_repository(context)
            task_id = context.user_data["notes_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("notes_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text == ".":
                context.user_data["notes_flow"]["notes"] = task.notes
            elif text.lower() in {"borrar", "eliminar", "sin notas"}:
                context.user_data["notes_flow"]["notes"] = None
            else:
                context.user_data["notes_flow"]["notes"] = text
            
            context.user_data["awaiting"] = "notes_resources"
            await update.message.reply_text(
                f"🔗 *Materiales y Recursos Académicos*\n\n"
                f"Escribe los enlaces o URLs de material de apoyo (separados por comas si son varios).\n"
                f"(escribe '.' para mantener: \"{task.resources or 'Ninguno'}\")",
                parse_mode="Markdown",
                reply_markup=MENU_KEYBOARD
            )
            return

        elif awaiting == "notes_resources":
            repository = get_repository(context)
            task_id = context.user_data["notes_flow"]["task_id"]
            task = repository.get_task(update.effective_user.id, task_id)
            if not task:
                context.user_data.pop("notes_flow", None)
                context.user_data.pop("awaiting", None)
                await update.message.reply_text("❌ Tarea no encontrada.", reply_markup=MENU_KEYBOARD)
                return

            if text == ".":
                resources = task.resources
            elif text.lower() in {"borrar", "eliminar", "sin recursos"}:
                resources = None
            else:
                resources = text
            
            notes = context.user_data["notes_flow"]["notes"]
            
            success = repository.update_task_notes_resources(update.effective_user.id, task_id, notes, resources)
            context.user_data.pop("notes_flow", None)
            context.user_data.pop("awaiting", None)
            
            if success:
                refresh_user_calendar(context, update.effective_user.id)
                await update.message.reply_text(
                    f"✅ *Notas y recursos guardados correctamente* para la tarea *\"{task.description}\"*.",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
            else:
                await update.message.reply_text(
                    "❌ No se pudieron guardar las notas.",
                    reply_markup=MENU_KEYBOARD
                )
            return

        elif awaiting == "postpone_date":
            task_id = context.user_data.pop("postpone_task_id", None)
            normalized_date = normalize_due_date(text)
            if not normalized_date:
                # Keep state and ask again
                context.user_data["awaiting"] = "postpone_date"
                context.user_data["postpone_task_id"] = task_id
                await update.message.reply_text(
                    "❌ *Fecha inválida.*\n\n"
                    "Por favor, ingresa una fecha correcta (ej. `25-06-2026`, `25/06/2026`, `mañana`, `en 3 días`):",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
                return
            
            repository = get_repository(context)
            due_date_obj = date.fromisoformat(normalized_date)
            was_postponed = repository.postpone_task(update.effective_user.id, task_id, due_date_obj)
            context.user_data.pop("awaiting", None)
            
            if was_postponed:
                # Recalculate priority automatically
                repository._auto_update_priorities(update.effective_user.id)
                refresh_user_calendar(context, update.effective_user.id)
                # Get updated task
                task = repository.get_task(update.effective_user.id, task_id)
                task_desc = task.description if task else "Tarea"
                await update.message.reply_text(
                    f"⏳ Tarea *\"{task_desc}\"* pospuesta para el *{normalized_date}*.\n\n"
                    f"Prioridad recalculada: {get_priority_emoji(task.priority)} {task.priority}",
                    parse_mode="Markdown",
                    reply_markup=MENU_KEYBOARD
                )
            else:
                await update.message.reply_text(
                    "❌ No se pudo posponer la tarea. Asegúrate de que aún esté pendiente.",
                    reply_markup=MENU_KEYBOARD
                )
            return

        # compatibility fallbacks
        if awaiting == "task_description":
            context.user_data.pop("awaiting", None)
            await save_task(update, context, parse_task_input(text))
            return

        if awaiting == "edit_task":
            task_id = context.user_data.pop("edit_task_id", None)
            context.user_data.pop("awaiting", None)
            await update_task_from_input(update, context, task_id, parse_task_input(text))
            return

        if awaiting == "task_notes":
            task_id = context.user_data.pop("notes_task_id", None)
            context.user_data.pop("awaiting", None)
            parts = [p.strip() for p in text.split("|")]
            notes = parts[0] if parts else None
            resources = parts[1] if len(parts) > 1 else None
            repository = get_repository(context)
            success = repository.update_task_notes_resources(update.effective_user.id, task_id, notes, resources)
            if success:
                refresh_user_calendar(context, update.effective_user.id)
                await update.message.reply_text(
                    f"✅ Notas y recursos actualizados.",
                    reply_markup=MENU_KEYBOARD,
                )
            else:
                await update.message.reply_text(
                    "❌ No se pudieron guardar las notas.",
                    reply_markup=MENU_KEYBOARD,
                )
            return

    if action == "menu_trigger":
        menu_text = (
            "📖 *Menú Principal*\n\n"
            "1. ➕ Agregar tarea\n"
            "2. 📋 Mostrar tareas\n"
            "3. ⏰ Vencidas\n"
            "4. 📅 Calendario\n"
            "5. 📊 Estadísticas\n"
            "6. ❓ Ayuda"
        )
        await update.message.reply_text(
            menu_text,
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
        )
        return

    if action == "1":
        context.user_data["create_flow"] = {}
        context.user_data["awaiting"] = "create_name"
        await update.message.reply_text(
            "📚 *Vamos a registrar una nueva tarea.*\n\n"
            "¿Cuál es el nombre de la actividad?",
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
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


async def send_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, tasks: list[Task]) -> None:
    await update.message.reply_text(title, reply_markup=MENU_KEYBOARD)
    for index, task in enumerate(tasks, start=1):
        formatted = format_task_with_index(task, index)
        await update.message.reply_text(formatted, reply_markup=task_action_keyboard(task.id))


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
    if normalized in {"menu", "menu principal", "menu."}:
        return "menu_trigger"
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

    for date_format in (
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %m %Y", "%Y/%m/%d", "%Y.%m.%d",
        "%d-%m-%y", "%d/%m/%y", "%d.%m.%y", "%d %m %y", "%y-%m-%d", "%y/%m/%d", "%y.%m.%d"
    ):
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
    if normalized in {"sin", "no", "ninguno", "sin recordatorio", "sin recordatorios", "desactivar"}:
        return 0
    if normalized in {"diario", "cada dia", "cada 1 dia", "1 dia", "diariamente"}:
        return 1440
    if normalized in {"hora", "cada hora", "cada 1 hora", "1 hora"}:
        return 60
    if normalized in {"cada semana", "semanal", "cada 1 semana", "1 semana", "semana"}:
        return 10080
    if normalized in {"cada 2 semanas", "2 semanas", "cada 2 sem", "2 sem"}:
        return 20160

    # Pattern matches
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(min|minuto|minutos)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)))
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(h|hora|horas)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)) * 60)
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(dia|dias)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)) * 1440)
    match = re.fullmatch(r"(?:cada\s+)?(\d+)\s*(semana|semanas|sem)", normalized)
    if match:
        return clamp_reminder_minutes(int(match.group(1)) * 10080)
    return None


def clamp_reminder_minutes(minutes: int) -> int:
    return max(5, min(minutes, 40320))


def format_reminder_interval(minutes: int) -> str:
    if minutes <= 0:
        return "sin recordatorio"
    if minutes == 10080:
        return "cada semana"
    if minutes == 20160:
        return "cada 2 semanas"
    if minutes == 1440:
        return "diario"
    if minutes % 10080 == 0:
        weeks = minutes // 10080
        return f"cada {weeks} semanas"
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
    due_date_str = task.due_date or "Sin fecha"
    if task.due_date and task.due_time:
        due_date_str += f" a las {format_24h_to_12h(task.due_time)}"
    return (
        f"#{task.id} {task.description}\n"
        f"Materia: {task.subject}\n"
        f"Prioridad: {task.priority}\n"
        f"Fecha: {due_date_str}\n"
        f"Recordatorio: {format_reminder_interval(task.reminder_minutes)}"
    )


def format_task_inline(task: Task) -> str:
    due_date_str = task.due_date or "sin fecha"
    if task.due_date and task.due_time:
        due_date_str += f" {format_24h_to_12h(task.due_time)}"
    return (
        f"{task.description} | {due_date_str} | {task.subject} | "
        f"{task.priority} | {format_reminder_interval(task.reminder_minutes)}"
    )


def get_status_emoji(status: str) -> str:
    if status == "Completada":
        return "🟢"
    if status == "En progreso":
        return "🟡"
    if status == "Vencida":
        return "⚫"
    return "🔴"


def get_priority_emoji(priority: str) -> str:
    if priority == "Crítica":
        return "🚨"
    if priority == "Alta":
        return "🔴"
    if priority == "Media":
        return "🟡"
    return "🟢"


def resolve_task_id(user_id: int, input_id_text: str, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    try:
        val = int(input_id_text.strip())
    except ValueError:
        return None
    if val <= 0:
        return None
    
    # 1. Try resolving using listed IDs in context session
    task_ids = context.user_data.get("user_task_ids")
    if task_ids and val <= len(task_ids):
        return task_ids[val - 1]
    
    # 2. Fallback: Query pending tasks from database and resolve by their sorted index
    try:
        repository = get_repository(context)
        tasks = repository.list_pending(user_id)
        if val <= len(tasks):
            return tasks[val - 1].id
    except Exception:
        pass
        
    return val


def should_notify_intelligent(task: Task, now: datetime) -> bool:
    if task.reminder_minutes <= 0:
        return False
    if not task.due_date:
        return False
    try:
        due_time_str = task.due_time or "23:59"
        due_dt = datetime.fromisoformat(f"{task.due_date}T{due_time_str}")
        time_diff = due_dt - now
        hours_diff = time_diff.total_seconds() / 3600.0
        if hours_diff <= 0:
            return False
            
        user_interval = task.reminder_minutes
        if hours_diff > 168.0:
            # More than 7 days: weekly max frequency
            adapted_interval = max(user_interval, 10080)
        elif hours_diff > 72.0:
            # 3 to 7 days: max frequency every 2 days
            adapted_interval = max(user_interval, 2880)
        elif hours_diff > 24.0:
            # 1 to 3 days: max frequency daily
            adapted_interval = max(user_interval, 1440)
        else:
            # Under 24h: increase frequency if user set it very low
            if hours_diff <= 1.0:
                adapted_interval = min(user_interval, 10)  # at least every 10 min
            elif hours_diff <= 6.0:
                adapted_interval = min(user_interval, 60)  # at least every hour
            else:
                adapted_interval = min(user_interval, 120)  # at least every 2 hours
                
        if not task.last_notified_at:
            return True
        last_notification = datetime.fromisoformat(task.last_notified_at)
        return now - last_notification >= timedelta(minutes=adapted_interval)
    except Exception:
        if not task.last_notified_at:
            return True
        try:
            last_notification = datetime.fromisoformat(task.last_notified_at)
            return now - last_notification >= timedelta(minutes=task.reminder_minutes)
        except Exception:
            return True


def format_task_with_index(task: Task, index: int) -> str:
    due_date_str = task.due_date or "Sin fecha"
    if task.due_date and task.due_time:
        due_date_str += f" (a las {format_24h_to_12h(task.due_time)})"
    return (
        f"#{index} {task.description}\n"
        f"📚 Materia: {task.subject}\n"
        f"🔥 Prioridad: {get_priority_emoji(task.priority)} {task.priority}\n"
        f"📊 Estado: {get_status_emoji(task.status)} {task.status}\n"
        f"📅 Fecha: {due_date_str}\n"
        f"⏰ Recordatorio: {format_reminder_interval(task.reminder_minutes)}"
    )


def task_action_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Completar", callback_data=f"done:{task_id}"),
                InlineKeyboardButton("✏️ Editar", callback_data=f"edit:{task_id}"),
            ],
            [
                InlineKeyboardButton("⏳ Posponer", callback_data=f"postpone:{task_id}"),
                InlineKeyboardButton("❌ Eliminar", callback_data=f"delete:{task_id}"),
            ],
            [
                InlineKeyboardButton("📝 Notas/Recursos", callback_data=f"notes:{task_id}"),
                InlineKeyboardButton("📋 Detalles", callback_data=f"detail:{task_id}"),
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
