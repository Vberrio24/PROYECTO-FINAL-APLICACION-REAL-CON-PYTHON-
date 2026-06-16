# Asistente Academico

Bot de Telegram para organizar tareas academicas con recordatorios, tareas vencidas y calendario visual.

## Problema que resuelve

Muchos estudiantes pierden fechas importantes porque las tareas quedan repartidas entre chats, cuadernos y recordatorios personales. Asistente Academico centraliza los pendientes en Telegram y permite registrar, consultar, editar, completar, posponer y eliminar tareas desde una interfaz sencilla.

## Funcionalidades

- Registrar tareas con descripcion, fecha limite, materia, prioridad y frecuencia de recordatorio.
- Editar tareas desde el boton `Editar` o con el comando `/editar`.
- Consultar tareas vencidas con `/vencidas`.
- Generar un calendario visual HTML con dias de la semana, meses, prioridades y tareas por dia.
- Completar, posponer o eliminar tareas con botones interactivos de Telegram.
- Ver estadisticas personales: pendientes, completadas, vencidas y materias con mas carga.
- Enviar recordatorios automaticos de tareas que vencen hoy o manana.
- Manejar errores de comandos incompletos, identificadores invalidos, fechas no reconocidas y recordatorios invalidos.

## Tecnologias

- Python 3.11+
- python-telegram-bot
- SQLite
- python-dotenv
- HTML/CSS para calendario visual

## Estructura

```text
src/asistente_academico/
  bot.py              # Comandos, menu, recordatorios y botones
  calendar_export.py  # Generacion del calendario visual HTML
  config.py           # Variables de entorno
  database.py         # Persistencia en SQLite
  __main__.py         # Punto de entrada
tests/                # Pruebas automaticas
```

## Instalacion local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

En `.env`:

```env
TELEGRAM_BOT_TOKEN=token_entregado_por_botfather
DATABASE_PATH=data/asistente_academico.sqlite3
CALENDAR_DIR=data/calendarios
```

## Ejecucion

```bash
python -m src.asistente_academico
```

## Menu del bot

1. Agregar tarea
2. Mostrar tareas
3. Vencidas
4. Calendario
5. Estadisticas
6. Ayuda

Las acciones de una tarea se manejan con botones: `Completar`, `Editar`, `Posponer` y `Eliminar`.

## Comandos

- `/agregar <tarea> | <fecha> | <materia> | <prioridad> | <recordatorio>`
- `/editar <id> | <tarea> | <fecha> | <materia> | <prioridad> | <recordatorio>`
- `/tareas`
- `/vencidas`
- `/calendario`
- `/hecho <id>`
- `/borrar <id>`
- `/stats`
- `/ayuda`

Ejemplos:

```text
/agregar Preparar demo | mañana | Programacion Avanzada | alta | cada 2 horas
/editar 3 | Preparar demo final | 16 de junio | Programacion Avanzada | alta | diario
```

## Calendario visual

El bot genera automaticamente un archivo HTML por usuario en:

```text
data/calendarios/
```

Para recibirlo por Telegram:

```text
/calendario
```

El calendario es visual, con dias de la semana, meses, colores por prioridad y tareas ubicadas en su fecha.

## Pruebas

```bash
python -m unittest discover -s tests
```

## Problemas comunes

Si aparece `telegram.error.TimedOut: Timed out`, el bot no pudo conectarse a Telegram a tiempo. Revisa internet, VPN, firewall y token. El bot intenta reconectarse automaticamente.

## Despliegue 

El bot depende de VS Code. Debe ejecutarse a traves de la consola para poder funcionar pero este proyecto puede ser escalable colocandolo a funcionar 24/7 si se conecta u un servidor con railway y otros.

Start command:

```bash
python -m src.asistente_academico
```

## Uso de IA

Se uso asistencia de IA como chatgpt para planear el alcance del proyecto, mejorar la estructura, redactar documentacion y apoyar la implementacion.

## Integrantes

- ESTIVEN SANTANA CUADRADO
- YEIFER MEDINA SIMANCAS 
- VICTOR ANDRES BERRRIO
- JORGUE ESCUDERO

