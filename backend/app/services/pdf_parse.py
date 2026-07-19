from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import get_settings

logger = logging.getLogger(__name__)

Route = Literal["digital", "scan", "vision"]

_ocr_engine: Any | None = None


@dataclass
class LayoutSpan:
    text: str
    bbox: list[float] | None = None  # [x0,y0,x1,y1]
    font_size: float | None = None
    role_hint: str | None = None


@dataclass
class PageImage:
    index: int
    bbox: list[float] | None = None
    area_ratio: float = 0.0
    png_bytes: bytes | None = None
    description: str | None = None


@dataclass
class PageBundle:
    page_no: int  # 1-based
    route: Route
    text: str
    spans: list[LayoutSpan] = field(default_factory=list)
    images: list[PageImage] = field(default_factory=list)
    char_count: int = 0


@dataclass
class ParseResult:
    pages: list[PageBundle]
    page_count: int
    full_text: str
    routes_used: dict[str, int] = field(default_factory=dict)

    @property
    def ocr_used(self) -> bool:
        return (self.routes_used.get("scan") or 0) > 0

    @property
    def vision_used(self) -> bool:
        return (self.routes_used.get("vision") or 0) > 0


def _get_ocr_engine() -> Any:
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def _ocr_image(img_bytes: bytes) -> str:
    engine = _get_ocr_engine()
    result = engine(img_bytes)
    lines: list[str] = []
    txts = getattr(result, "txts", None)
    if txts:
        lines = [str(t).strip() for t in txts if str(t).strip()]
    elif isinstance(result, (list, tuple)):
        rows = result[0] if result and isinstance(result[0], list) else result
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                text = str(row[1]).strip()
                if text:
                    lines.append(text)
    return "\n".join(lines)


def _classify_page(char_count: int, image_ratio: float, min_chars: int, vision_ratio: float) -> Route:
    if char_count >= min_chars:
        if image_ratio >= vision_ratio and char_count < min_chars * 3:
            return "vision"
        return "digital"
    if image_ratio >= vision_ratio:
        return "vision"
    return "scan"


def parse_pdf_pages(data: bytes) -> ParseResult:
    """L1: per-page route digital / scan / vision, with real page numbers."""
    import fitz

    settings = get_settings()
    min_chars = max(1, settings.ocr_min_chars_per_page)
    vision_ratio = float(settings.vision_min_image_ratio)
    max_pages = max(1, settings.ocr_max_pages)
    dpi = settings.ocr_render_dpi
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    doc = fitz.open(stream=data, filetype="pdf")
    pages_out: list[PageBundle] = []
    routes_used: dict[str, int] = {"digital": 0, "scan": 0, "vision": 0}

    try:
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc.load_page(i)
            page_no = i + 1
            rect = page.rect
            page_area = max(1.0, float(rect.width * rect.height))

            # digital text + spans
            spans: list[LayoutSpan] = []
            try:
                raw = page.get_text("dict")
                for block in raw.get("blocks") or []:
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines") or []:
                        for span in line.get("spans") or []:
                            t = (span.get("text") or "").strip()
                            if not t:
                                continue
                            bbox = list(span.get("bbox") or []) if span.get("bbox") else None
                            spans.append(
                                LayoutSpan(
                                    text=t,
                                    bbox=bbox,
                                    font_size=float(span.get("size") or 0) or None,
                                )
                            )
            except Exception:  # noqa: BLE001
                spans = []

            digital_text = page.get_text("text") or ""
            if not digital_text.strip() and spans:
                digital_text = "\n".join(s.text for s in spans)

            # image area ratio
            images: list[PageImage] = []
            img_area = 0.0
            try:
                for img_i, img in enumerate(page.get_images(full=True) or []):
                    try:
                        rects = page.get_image_rects(img[0])
                    except Exception:  # noqa: BLE001
                        rects = []
                    for r in rects or []:
                        area = abs(float(r.width * r.height))
                        img_area += area
                        images.append(
                            PageImage(
                                index=img_i,
                                bbox=[float(r.x0), float(r.y0), float(r.x1), float(r.y1)],
                                area_ratio=area / page_area,
                            )
                        )
            except Exception:  # noqa: BLE001
                pass
            image_ratio = min(1.0, img_area / page_area)

            route = _classify_page(len(digital_text.strip()), image_ratio, min_chars, vision_ratio)
            if route == "vision" and not settings.vision_enabled:
                route = "scan" if len(digital_text.strip()) < min_chars else "digital"
            if route == "scan" and not settings.ocr_enabled:
                route = "digital"

            text = digital_text.strip()
            if route == "scan":
                try:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    ocr_text = _ocr_image(pix.tobytes("png"))
                    if ocr_text.strip():
                        text = ocr_text.strip()
                except Exception:  # noqa: BLE001
                    logger.exception("OCR failed page=%s", page_no)
            elif route == "vision":
                # Capture largest image for optional vision describe later
                try:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    png = pix.tobytes("png")
                    if images:
                        images[0].png_bytes = png
                    else:
                        images.append(PageImage(index=0, area_ratio=image_ratio, png_bytes=png))
                    # Prefer OCR body + placeholder; vision enrich in structure/vision step
                    if len(text) < min_chars:
                        ocr_text = _ocr_image(png)
                        if ocr_text.strip():
                            text = ocr_text.strip()
                    if settings.vision_enabled:
                        desc = _vision_describe_page(png, page_no)
                        if desc:
                            for im in images:
                                if im.png_bytes:
                                    im.description = desc
                                    break
                            text = (text + "\n\n[图/版面视觉描述]\n" + desc).strip()
                except Exception:  # noqa: BLE001
                    logger.exception("vision route failed page=%s", page_no)

            routes_used[route] = routes_used.get(route, 0) + 1
            pages_out.append(
                PageBundle(
                    page_no=page_no,
                    route=route,
                    text=text,
                    spans=spans if route == "digital" else [],
                    images=images,
                    char_count=len(text),
                )
            )

        # remaining pages beyond OCR cap: digital-only best effort
        for i in range(n, len(doc)):
            page = doc.load_page(i)
            text = (page.get_text("text") or "").strip()
            routes_used["digital"] = routes_used.get("digital", 0) + 1
            pages_out.append(
                PageBundle(page_no=i + 1, route="digital", text=text, char_count=len(text))
            )

        full_parts = []
        for p in pages_out:
            if p.text.strip():
                full_parts.append(f"\n\n--- page {p.page_no} ---\n\n{p.text.strip()}")
        full_text = "\n".join(full_parts).strip()
        return ParseResult(
            pages=pages_out,
            page_count=len(doc),
            full_text=full_text,
            routes_used=routes_used,
        )
    finally:
        doc.close()


def _vision_describe_page(png_bytes: bytes, page_no: int) -> str | None:
    """Optional multimodal describe; returns None if unavailable."""
    settings = get_settings()
    if not settings.vision_enabled:
        return None
    api_key = (settings.vision_api_key or settings.embedding_api_key or settings.openai_api_key or "").strip()
    base = (settings.vision_base_url or settings.embedding_base_url or settings.openai_base_url or "").rstrip("/")
    if not api_key or not base:
        logger.warning("vision enabled but missing VISION_/EMBEDDING_/OPENAI credentials")
        return None
    try:
        import base64

        import httpx

        b64 = base64.b64encode(png_bytes).decode("ascii")
        model = (settings.vision_model or "glm-4v-flash").strip()
        url = f"{base}/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"这是论文 PDF 第 {page_no} 页的渲染图。"
                                "用简体中文简要描述图中的图表、坐标轴、关键数值与结论线索，不要编造看不见的内容。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.warning("vision describe HTTP %s %s", resp.status_code, resp.text[:200])
                return None
            return (resp.json()["choices"][0]["message"]["content"] or "").strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("vision describe failed")
        return None


def extract_pdf_text(data: bytes) -> tuple[str, int, bool]:
    """Backward-compatible wrapper. Returns (full_text, page_count, ocr_used)."""
    result = parse_pdf_pages(data)
    return result.full_text, result.page_count, result.ocr_used
