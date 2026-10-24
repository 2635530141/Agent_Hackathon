"""Request-local host dependencies; never change the global synthetic owner."""
from contextvars import ContextVar

calendar_context = ContextVar("calendar_context", default=None)


def owner_id():
    context = calendar_context.get()
    if context is not None:
        return context.actor.subject_id
    from app import store
    return store.OWNER_ID


def live_context():
    return calendar_context.get()
