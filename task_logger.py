"""
Task-aware structured logging for GABS API.

Provides thread-local task context that is automatically injected into every
log record by a custom logging.Filter, enabling log grouping by operation.

Usage:
    from task_logger import set_task_context, clear_task_context

    set_task_context("manual_booking", user="france@...", class_name="Blitz",
                     date="2026-03-31", time="09:20")
    # ... all logging.info/warning/error calls in this thread now carry context
    clear_task_context()
"""
import threading
import uuid
import json
import logging
from datetime import datetime


# Thread-local storage for task context
_task_context = threading.local()


def set_task_context(scenario: str, user: str = None, class_name: str = None,
                     date: str = None, time: str = None, **extra):
    """Set the current task context for this thread.

    All subsequent log calls in this thread will carry these fields
    until clear_task_context() is called.
    """
    _task_context.task_id = uuid.uuid4().hex[:8]
    _task_context.scenario = scenario
    _task_context.user = user
    _task_context.class_name = class_name
    _task_context.date = date
    _task_context.time = time
    _task_context.extra = extra
    return _task_context.task_id


def clear_task_context():
    """Remove all task context from this thread."""
    for attr in ['task_id', 'scenario', 'user', 'class_name',
                 'date', 'time', 'extra']:
        if hasattr(_task_context, attr):
            delattr(_task_context, attr)


def get_task_context() -> dict:
    """Get the current task context (or empty dict if none set)."""
    ctx = {}
    for attr in ['task_id', 'scenario', 'user', 'class_name', 'date', 'time']:
        val = getattr(_task_context, attr, None)
        if val is not None:
            ctx[attr] = val
    extra = getattr(_task_context, 'extra', {})
    if extra:
        ctx.update(extra)
    return ctx


class TaskContextFilter(logging.Filter):
    """Logging filter that injects task context into every log record."""

    def filter(self, record):
        ctx = get_task_context()
        record.task_id = ctx.get('task_id', '')
        record.scenario = ctx.get('scenario', '')
        record.task_user = ctx.get('user', '')
        record.task_class = ctx.get('class_name', '')
        record.task_date = ctx.get('date', '')
        record.task_time = ctx.get('time', '')
        record.task_extra = {
            k: v for k, v in ctx.items()
            if k not in ('task_id', 'scenario', 'user',
                         'class_name', 'date', 'time')
        }
        return True


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON Lines, including task context."""

    def format(self, record):
        log_entry = {
            'ts': datetime.fromtimestamp(record.created).strftime(
                '%Y-%m-%d %H:%M:%S,%f')[:-3],
            'level': record.levelname,
            'msg': record.getMessage(),
        }

        # Add task context fields (only if set)
        if getattr(record, 'task_id', ''):
            log_entry['task_id'] = record.task_id
        if getattr(record, 'scenario', ''):
            log_entry['scenario'] = record.scenario
        if getattr(record, 'task_user', ''):
            log_entry['user'] = record.task_user
        if getattr(record, 'task_class', ''):
            log_entry['class'] = record.task_class
        if getattr(record, 'task_date', ''):
            log_entry['date'] = record.task_date
        if getattr(record, 'task_time', ''):
            log_entry['time'] = record.task_time

        extra = getattr(record, 'task_extra', {})
        if extra:
            log_entry['extra'] = extra

        # Include exception info if present
        if record.exc_info and record.exc_text is None:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_entry['exception'] = record.exc_text

        return json.dumps(log_entry, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """Formats log records in a human-readable format with task context.

    Used for console output so developers can still read logs in the terminal.
    """

    def format(self, record):
        ts = datetime.fromtimestamp(record.created).strftime(
            '%Y-%m-%d %H:%M:%S,%f')[:-3]
        task_id = getattr(record, 'task_id', '')
        scenario = getattr(record, 'scenario', '')
        user = getattr(record, 'task_user', '')

        # Build context prefix
        parts = []
        if task_id:
            parts.append(f'[{task_id}]')
        if scenario:
            parts.append(f'[{scenario}]')
        if user:
            parts.append(user)

        prefix = ' '.join(parts)
        if prefix:
            prefix = f' {prefix} |'

        line = f'{ts} - {record.levelname}{prefix} {record.getMessage()}'

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line += f'\n{record.exc_text}'

        return line
