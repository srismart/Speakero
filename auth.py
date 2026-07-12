import os
from dataclasses import dataclass

import jwt


@dataclass
class AuthContext:
    user_id: str
    email: str
    tier: str  # "free" | "pro"


def verify_token(authorization: str | None) -> AuthContext | None:
    """Verify a Supabase access token (HS256). Returns None for anything
    invalid or unconfigured: callers treat None as anonymous."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
    except jwt.InvalidTokenError:
        return None
    tier = (payload.get("app_metadata") or {}).get("tier", "free")
    if tier not in ("free", "pro"):
        tier = "free"
    return AuthContext(user_id=payload.get("sub", ""), email=payload.get("email", ""), tier=tier)
