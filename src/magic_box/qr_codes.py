"""Small QR-code helpers for free/open fallback links."""

from __future__ import annotations

from io import BytesIO


class QRCodeError(RuntimeError):
    """Raised when a QR code cannot be generated."""


def make_qr_svg(data: str, *, title: str = "Story Dock QR code") -> bytes:
    """Return an SVG QR code for a URL or short string."""

    cleaned = data.strip()
    if not cleaned:
        raise QRCodeError("QR code data is required.")

    try:
        import segno
    except ImportError as exc:  # pragma: no cover - exercised only on bad installs
        raise QRCodeError("Install the segno package to generate QR codes.") from exc

    output = BytesIO()
    qr = segno.make(cleaned, error="m", micro=False)
    qr.save(
        output,
        kind="svg",
        scale=6,
        border=3,
        xmldecl=False,
        svgns=True,
        title=title,
    )
    return output.getvalue()
