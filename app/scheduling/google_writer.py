"""Only app-linked primary-calendar events; no attendees or arbitrary IDs from HTTP."""
import httpx
from .google_config import WRITE_SCOPE
from .google_provider import EVENTS_URL
from .google_oauth import protect_provider_logs
from .export_models import ExportError

WRITE_FIELDS = "id,status,etag,htmlLink,summary,location,start,end,extendedProperties"


class GoogleEventWriter:
    is_live = True

    def __init__(self, oauth, transport=None):
        self.oauth, self.transport = oauth,transport
        protect_provider_logs()

    def binding(self, owner_id):
        token,generation,_ = self.oauth.vault.load(owner_id)
        if WRITE_SCOPE not in token.get("scope","").split():
            raise ExportError("WRITE_SCOPE_REQUIRED")
        if not token.get("account_subject"):
            raise ExportError("GOOGLE_ACCOUNT_BINDING_REQUIRED")
        return token["account_subject"],generation

    def _request(self, method, owner_id, event_id=None, **kwargs):
        with self.oauth.owner_lock(owner_id):
            return self._locked_request(method,owner_id,event_id,**kwargs)

    def _locked_request(self, method, owner_id, event_id=None, **kwargs):
        import re
        if event_id is not None and not re.fullmatch(r"smcal[a-f0-9]{64}",event_id):
            raise ExportError("APP_EVENT_ID_REQUIRED",403)
        self.binding(owner_id)
        try:
            token = self.oauth.access_token(owner_id)
            headers = {"Authorization":"Bearer "+token,**kwargs.pop("headers",{})}
            with httpx.Client(transport=self.transport,timeout=10) as client:
                response = client.request(method,EVENTS_URL+("/"+event_id if event_id else ""),
                    headers=headers,**kwargs)
            if response.status_code==401:
                self.oauth.access_token(owner_id,force=True)
                raise ExportError("PROVIDER_RESULT_UNKNOWN",202)
            if response.status_code==403:
                raise ExportError("WRITE_SCOPE_REQUIRED")
            return response
        except ExportError:
            raise
        except Exception:
            raise ExportError("PROVIDER_RESULT_UNKNOWN",202) from None

    def get_event(self, owner_id, event_id):
        response = self._request("GET",owner_id,event_id,params={"fields":WRITE_FIELDS})
        if response.status_code in {404,410}:
            return None
        if response.status_code!=200:
            raise ExportError("PROVIDER_RESULT_UNKNOWN",202)
        try:
            result = response.json()
            return None if result.get("status")=="cancelled" else result
        except (ValueError,TypeError,AttributeError):
            raise ExportError("PROVIDER_RESULT_UNKNOWN",202) from None

    def create_event(self, owner_id, body):
        allowed = {"id","summary","location","description","start","end","reminders","extendedProperties"}
        if set(body)!=allowed or not body.get("id","").startswith("smcal"):
            raise ExportError("APP_EVENT_PAYLOAD_REQUIRED",403)
        response = self._request("POST",owner_id,json=body,
            params={"sendUpdates":"none","fields":WRITE_FIELDS})
        if response.status_code in {200,201}:
            try:
                return response.json()
            except ValueError:
                raise ExportError("PROVIDER_RESULT_UNKNOWN",202) from None
        if response.status_code==409:
            return None  # Caller must GET and verify; conflict is never proof of success.
        raise ExportError("PROVIDER_RESULT_UNKNOWN",202)  # No blind POST retry after ambiguity.

    def delete_event(self, owner_id, event_id, etag):
        response = self._request("DELETE",owner_id,event_id,params={"sendUpdates":"none"},
            headers={"If-Match":etag})
        if response.status_code not in {204,404,410}:
            raise ExportError("REMOTE_EVENT_CHANGED" if response.status_code==412 else "PROVIDER_RESULT_UNKNOWN",202)
