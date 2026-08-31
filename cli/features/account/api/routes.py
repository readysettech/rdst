"""Local API routes for Readyset account browser sign-in."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from features.account.service import AccountServiceError, account_service
from shared.api.guards import require_local_request
from shared.run_registry import run_registry

router = APIRouter()


def _browser_callback_page(succeeded: bool) -> str:
    title = "You're signed in" if succeeded else "Sign-in failed"
    message = (
        "RDST is ready. Return to the app and close this tab."
        if succeeded
        else "Return to RDST to see the error and try again."
    )
    tone = "#a6f4c5" if succeeded else "#ffb4ab"
    icon = "&#10003;" if succeeded else "!"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{title} | Readyset</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: #f4f1f1; background: radial-gradient(circle at 50% 10%, #30274d 0, #191718 42%, #111011 100%); }}
    main {{ width: min(100%, 460px); padding: 36px; border: 1px solid #3a3638; border-radius: 24px; background: rgba(29, 27, 28, .94); box-shadow: 0 24px 70px rgba(0, 0, 0, .38); text-align: center; }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; margin-bottom: 30px; color: #f4f1f1; font-size: 16px; font-weight: 650; letter-spacing: -.01em; }}
    .mark {{ width: 26px; height: 26px; border-radius: 7px; background: linear-gradient(135deg, #8d72ff, #5b47b7); position: relative; }}
    .mark::before, .mark::after {{ content: ""; position: absolute; width: 8px; height: 8px; background: white; }}
    .mark::before {{ left: 5px; bottom: 5px; }}
    .mark::after {{ right: 5px; top: 5px; }}
    .status {{ width: 58px; height: 58px; margin: 0 auto 20px; display: grid; place-items: center; border-radius: 18px; color: {tone}; background: color-mix(in srgb, {tone} 14%, transparent); border: 1px solid color-mix(in srgb, {tone} 35%, transparent); font-size: 28px; font-weight: 650; }}
    h1 {{ margin: 0 0 10px; font-size: 28px; line-height: 1.2; letter-spacing: -.025em; }}
    p {{ margin: 0; color: #b9b3b6; font-size: 16px; line-height: 1.6; }}
    .hint {{ margin-top: 26px; padding-top: 20px; border-top: 1px solid #383436; color: #8f898c; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <div class="brand"><span class="mark" aria-hidden="true"></span><span>Readyset</span></div>
    <div class="status" aria-hidden="true">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    <p class="hint">This tab no longer needs to stay open.</p>
  </main>
</body>
</html>"""


class AccountLoginRequest(BaseModel):
    return_url: str


class AccountLoginResponse(BaseModel):
    login_id: str
    state: str
    auth_url: str
    publishable_key: str
    callback_url: str
    expires_in: int


class AccountLoginCompleteRequest(BaseModel):
    login_id: str
    state: str
    access_token: str
    refresh_token: str
    expires_in: int = 3600


class AccountLoginStatusResponse(BaseModel):
    state: str
    detail: str


class AccountBrowserCallbackStatusResponse(BaseModel):
    state: str
    code: str | None = None
    error: str | None = None
    error_description: str | None = None


class AccountStatusResponse(BaseModel):
    signed_in: bool
    user_id: str | None = None
    email: str | None = None
    model: str | None = None
    quota: dict[str, Any] | None = None
    detail: str | None = None


class AccountLogoutResponse(BaseModel):
    success: bool


@router.post("/account/login", response_model=AccountLoginResponse)
async def start_account_login(
    request: Request, body: AccountLoginRequest
) -> AccountLoginResponse:
    require_local_request(request)
    try:
        result = await asyncio.to_thread(account_service.start_login, body.return_url)
    except AccountServiceError as exc:
        status_code = (
            exc.status_code
            if exc.status_code is not None and 400 <= exc.status_code < 500
            else 502
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return AccountLoginResponse(**result)


@router.get("/account/login/{login_id}/context", response_model=AccountLoginResponse)
async def account_login_context(
    request: Request, login_id: str
) -> AccountLoginResponse:
    require_local_request(request)
    try:
        result = account_service.login_context(login_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sign-in attempt not found") from exc
    return AccountLoginResponse(**result)


@router.get("/account/oauth/callback", response_class=HTMLResponse)
async def capture_account_oauth_callback(
    request: Request,
    login_id: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    """Hand an external-browser OAuth result back to the open RDST process."""
    require_local_request(request)
    try:
        account_service.record_browser_callback(
            login_id,
            code=code,
            error=error,
            error_description=error_description[:200],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sign-in attempt not found") from exc
    except AccountServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    succeeded = bool(code)
    return HTMLResponse(
        content=_browser_callback_page(succeeded),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/account/login/{login_id}/browser-callback",
    response_model=AccountBrowserCallbackStatusResponse,
)
async def account_browser_callback_status(
    request: Request, login_id: str
) -> AccountBrowserCallbackStatusResponse:
    require_local_request(request)
    try:
        result = account_service.browser_callback_status(login_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sign-in attempt not found") from exc
    return AccountBrowserCallbackStatusResponse(**result)


@router.post(
    "/account/login/complete", response_model=AccountLoginStatusResponse
)
async def complete_account_login(
    request: Request, body: AccountLoginCompleteRequest
) -> AccountLoginStatusResponse:
    require_local_request(request)
    try:
        result = await asyncio.to_thread(
            account_service.complete_login,
            body.login_id,
            body.state,
            body.access_token,
            body.refresh_token,
            body.expires_in,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sign-in attempt not found") from exc
    except AccountServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result["state"] == "success":
        run_registry.wake_needs_key()
    return AccountLoginStatusResponse(**result)


@router.get(
    "/account/login/{login_id}", response_model=AccountLoginStatusResponse
)
async def account_login_status(
    request: Request, login_id: str
) -> AccountLoginStatusResponse:
    require_local_request(request)
    try:
        result = await asyncio.to_thread(account_service.login_status, login_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sign-in attempt not found") from exc
    if result["state"] == "success":
        run_registry.wake_needs_key()
    return AccountLoginStatusResponse(**result)


@router.get("/account/status", response_model=AccountStatusResponse)
async def account_status(request: Request) -> AccountStatusResponse:
    require_local_request(request)
    result = await asyncio.to_thread(account_service.status, False)
    return AccountStatusResponse(**result)


@router.post("/account/logout", response_model=AccountLogoutResponse)
async def account_logout(request: Request) -> AccountLogoutResponse:
    require_local_request(request)
    await asyncio.to_thread(account_service.logout)
    return AccountLogoutResponse(success=True)
