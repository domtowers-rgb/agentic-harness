import re

from pptx import Presentation

from plugins.file_ops import _resolve_safe, safe_output_name, sandbox_dir
from plugins.word_document import DELIVERY_NOTE

MAX_SLIDES = 100
MAX_BULLETS_PER_SLIDE = 50


def _sanitize_filename(name: str) -> str:
    return safe_output_name(name, ".pptx", "presentation")


def _plain(text) -> str:
    """Slide text with any **bold** markers removed rather than shown."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(text))


def create_presentation(title: str, slides: list, subtitle: str = None, filename: str = None):
    """Create a .pptx file in the sandboxed working directory: a title slide
    followed by one title+bullets slide per entry in `slides`."""
    if not title or not isinstance(title, str):
        return {"error": "title is required"}
    if not isinstance(slides, list):
        return {"error": "slides must be a list of {title, bullets} objects"}
    if len(slides) > MAX_SLIDES:
        return {"error": f"too many slides (max {MAX_SLIDES})"}

    try:
        target = _resolve_safe(_sanitize_filename(filename or title))
    except ValueError as exc:
        return {"error": str(exc)}

    try:
        prs = Presentation()

        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = title
        if subtitle and len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = subtitle

        bullet_layout = prs.slide_layouts[1]
        for i, entry in enumerate(slides):
            if not isinstance(entry, dict):
                return {"error": f"slide {i} must be an object with 'title' and optional 'bullets'"}
            bullets = entry.get("bullets") or []
            if len(bullets) > MAX_BULLETS_PER_SLIDE:
                return {"error": f"slide {i} has too many bullets (max {MAX_BULLETS_PER_SLIDE})"}

            slide = prs.slides.add_slide(bullet_layout)
            slide.shapes.title.text = _plain(entry.get("title") or "")
            if bullets and len(slide.placeholders) > 1:
                text_frame = slide.placeholders[1].text_frame
                text_frame.text = _plain(bullets[0])
                for bullet in bullets[1:]:
                    text_frame.add_paragraph().text = _plain(bullet)

        target.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(target))
    except Exception as exc:
        return {"error": f"failed to create presentation: {exc}"}

    path = str(target.relative_to(sandbox_dir()))
    # "attachment" marks this as a file to hand back to the user - see
    # _run_tool_calls in agentic_harness/main.py.
    return {"status": "written", "path": path, "attachment": path, "slide_count": len(slides) + 1, "note": DELIVERY_NOTE}

