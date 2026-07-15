import hmac

from common.config import AdminApiSettings
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def get_admin_api_settings() -> AdminApiSettings:
    return AdminApiSettings()


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: AdminApiSettings = Depends(get_admin_api_settings),
) -> None:
    """PROJECT.md Section 11: static `ADMIN_API_KEY` bearer token, required
    on every route except `/health` and `/webhooks/freqtrade` (which has its
    own shared-secret auth). An unconfigured key always rejects — never
    silently open by omission."""
    if (
        not settings.admin_api_key
        or credentials is None
        or not hmac.compare_digest(credentials.credentials, settings.admin_api_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key"
        )
