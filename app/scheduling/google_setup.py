"""Explicit host composition; nothing here auto-loads or connects credentials."""
from pathlib import Path
from sqlalchemy import select
from .core.storage import markers
from .google_config import CALLBACK, GoogleConfig
from .google_integration import GoogleCalendarIntegration
from .google_oauth import GoogleOAuth
from .google_provider import GoogleCalendarProvider
from .google_storage import GoogleVault


def build_google_integration(repository, session_gateway, timezone_for_owner, *,
                             client_file, encryption_key_file, redirect_uri=CALLBACK,
                             clock=None):
    with repository.engine.connect() as connection:
        seeded = connection.execute(select(markers.c.key)).scalars().all()
    if any(key in {"team-synthetic-v1","synthetic-fixture-v1"} for key in seeded):
        raise ValueError("Real OAuth requires an isolated authenticated repository, not the demo database")
    config = GoogleConfig.from_file(client_file,redirect_uri)
    vault = GoogleVault(repository.engine,Path(encryption_key_file).read_bytes().strip())
    vault.migrate()
    oauth = GoogleOAuth(config,vault,clock=clock)
    provider = GoogleCalendarProvider(oauth.access_token,timezone_for_owner,clock=clock,
                                      generation_for_owner=vault.generation)
    return GoogleCalendarIntegration(repository,vault,oauth,provider,session_gateway)


def build_export_service(active, session_gateway):
    """Explicit M3 composition; synthetic Session/store cannot authorize live writes."""
    from .export_storage import ExportRepository
    from .export_service import CalendarExportService
    from .google_writer import GoogleEventWriter
    if not getattr(session_gateway,"authoritative",False):
        raise ValueError("An authoritative revision-guarded SessionGateway is required")
    if active.service.sessions is not session_gateway:
        raise ValueError("Read/refresh/export must use the same authoritative SessionGateway")
    repository = ExportRepository(active.repository.engine)
    repository.migrate()
    return CalendarExportService(repository,session_gateway,GoogleEventWriter(active.oauth),
        active.service,active.refresh_calendars,active.oauth.clock)
