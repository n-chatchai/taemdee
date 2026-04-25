from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_shop
from app.core.database import get_session
from app.models import Shop
from app.routes.auth import _set_session_cookie
from app.services.auth import issue_session_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request=request, name="shop/register.html", context={})


@router.post("/register")
async def dev_login_or_register(
    request: Request,
    response: Response,
    phone: str = Form(...),
    name: str = Form("New Shop"),
    db: AsyncSession = Depends(get_session),
):
    """DEV-ONLY shortcut: the template's mock OTP flow lands here.

    Creates the shop if it doesn't exist, sets a session cookie, and redirects to the
    dashboard. Real OTP verification happens at `/auth/otp/verify` — this endpoint
    exists so the current demo UI keeps working until the proper OTP form replaces it.
    """
    result = await db.exec(select(Shop).where(Shop.phone == phone))
    shop = result.first()

    if not shop:
        shop = Shop(name=name, phone=phone)
        db.add(shop)
        await db.commit()
        await db.refresh(shop)

    redirect = RedirectResponse(url="/shop/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(redirect, issue_session_token(shop.id))
    return redirect


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    shop: Shop = Depends(get_current_shop),
):
    return templates.TemplateResponse(
        request=request,
        name="shop/dashboard.html",
        context={"shop": shop},
    )
