"""Verify only Google's stable subject; no email/profile scope or token logging."""
from secrets import compare_digest
import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from .google_storage import GoogleStateError

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


class GoogleIdentityVerifier:
    def __init__(self, client_id, clock, transport=None):
        self.client_id, self.clock, self.transport = client_id,clock,transport

    def __call__(self, encoded, nonce):
        try:
            with httpx.Client(transport=self.transport,timeout=10) as client:
                response = client.get(JWKS_URL)
                response.raise_for_status()
            keys = KeySet.import_key_set(response.json())
            claims = jwt.decode(encoded,keys,algorithms=["RS256"]).claims
            aud = claims["aud"] if isinstance(claims["aud"],list) else [claims["aud"]]
            now = self.clock().timestamp()
            if (claims["iss"] not in {"https://accounts.google.com","accounts.google.com"}
                or self.client_id not in aud or claims.get("azp",self.client_id)!=self.client_id
                or (len(aud)>1 and claims.get("azp")!=self.client_id)
                or not float(claims["iat"]) <= now+60 or not float(claims["exp"])>now
                or not compare_digest(str(claims["nonce"]).encode(),nonce.encode())
                or not isinstance(claims["sub"],str) or not 1<=len(claims["sub"])<=255):
                raise ValueError("claims")
            return claims["sub"]
        except Exception:
            raise GoogleStateError("GOOGLE_IDENTITY_NOT_VERIFIED") from None
