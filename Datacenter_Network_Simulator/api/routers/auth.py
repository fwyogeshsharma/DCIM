"""Authentication REST endpoints — login (open) + token check."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import TTL_SECONDS, authenticate, issue_token, require_auth

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    expires_in: int
    username: str
    role: str


@router.post("/login", response_model=TokenResponse, summary="Exchange username + password for a JWT")
def login(req: LoginRequest):
    claims = authenticate(req.username, req.password)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    return TokenResponse(
        token=issue_token(claims["username"], claims["role"]),
        expires_in=TTL_SECONDS,
        username=claims["username"],
        role=claims["role"],
    )


@router.get("/check", summary="Validate the current bearer token")
def check(claims: dict = Depends(require_auth)):
    """Returns the token's user if valid, 401 otherwise. Used by the web UI on load."""
    return {"ok": True, "username": claims.get("sub"), "role": claims.get("role", "user")}
