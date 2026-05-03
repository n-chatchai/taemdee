from fastapi import Request

from app.core.config import settings


def customer_base_url(request: Request) -> str:
    """Customer-facing base URL (e.g. https://taemdee.com).

    Use for QR codes that customers scan — the printed shop QR is generated
    while the shop owner is on shop.taemdee.com, but the customer who scans
    it should land on the customer domain.
    """
    return f"{request.url.scheme}://{settings.main_domain}"
