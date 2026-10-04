import os
import re
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from plugins.file_ops import _resolve_safe, safe_output_name, sandbox_dir
from plugins.word_document import DELIVERY_NOTE, MAX_ITEMS_PER_SECTION, MAX_SECTIONS, _as_text_list

# reportlab's built-in fonts only cover Western European characters -
# anything else (Greek, Cyrillic, many symbols) would come out as boxes.
# So a TrueType font with wide coverage is used where one is installed:
# DejaVu Sans (most Linux systems) or Arial (Windows). Regular + bold
# paths; the first pair found wins, else Helvetica.
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
]


def _register_font():
    """Name of the body font to use, registering a TrueType one (with its
    bold variant, so <b> works) if available."""
    for regular, bold in _FONT_CANDIDATES:
        if os.path.isfile(regular) and os.path.isfile(bold):
            try:
                pdfmetrics.registerFont(TTFont("DocSans", regular))
                pdfmetrics.registerFont(TTFont("DocSans-Bold", bold))
                pdfmetrics.registerFontFamily("DocSans", normal="DocSans", bold="DocSans-Bold")
                return "DocSans"
            except Exception:
                continue
    return "Helvetica"


FONT = _register_font()
BOLD_FONT = "DocSans-Bold" if FONT == "DocSans" else "Helvetica-Bold"


def _styles():
    styles = getSampleStyleSheet()
    for name in ("Title", "Heading1", "BodyText"):
        styles[name].fontName = BOLD_FONT if name != "BodyText" else FONT
    styles["BodyText"].fontSize = 11
    styles["BodyText"].leading = 15
    styles["BodyText"].spaceAfter = 6
    return styles


def _markup(text: str) -> str:
    """Plain text -> reportlab's paragraph markup: special characters
    escaped (it parses <, > and & as markup), **bold** kept as bold since
    models write it constantly, and line breaks kept."""
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text.replace("\n", "<br/>")


def _page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 9)
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


def create_pdf(title: str, sections: list, filename: str = None):
    """Create an A4 PDF in the sandboxed working directory: a title, then
    per entry in `sections` an optional heading followed by paragraphs and
    bullet points - the same shape create_document takes."""
    if not title or not isinstance(title, str):
        return {"error": "title is required"}
    if not isinstance(sections, list):
        return {"error": "sections must be a list of {heading, paragraphs, bullets} objects"}
    if len(sections) > MAX_SECTIONS:
        return {"error": f"too many sections (max {MAX_SECTIONS})"}

    try:
        target = _resolve_safe(safe_output_name(filename or title, ".pdf", "document"))
    except ValueError as exc:
        return {"error": str(exc)}

    styles = _styles()
    story = [Paragraph(_markup(title), styles["Title"])]
    try:
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                return {"error": f"section {i} must be an object with optional 'heading', 'paragraphs' and 'bullets'"}
            if section.get("heading"):
                story.append(Paragraph(_markup(str(section["heading"])), styles["Heading1"]))
            for paragraph in _as_text_list(section.get("paragraphs"), "paragraphs", i):
                story.append(Paragraph(_markup(paragraph), styles["BodyText"]))
            bullets = _as_text_list(section.get("bullets"), "bullets", i)
            if bullets:
                story.append(ListFlowable(
                    [ListItem(Paragraph(_markup(b), styles["BodyText"]), leftIndent=12) for b in bullets],
                    bulletType="bullet", bulletFontName=FONT, leftIndent=12,
                ))
                story.append(Spacer(1, 4))

        target.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(target), pagesize=A4, title=title,
            leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        )
        doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"failed to create PDF: {exc}"}

    path = str(target.relative_to(sandbox_dir()))
    # "attachment" marks this as a file to hand back to the user - see
    # _run_tool_calls in agentic_harness/main.py.
    return {"status": "written", "path": path, "attachment": path, "section_count": len(sections), "note": DELIVERY_NOTE}


def register(registry):
    registry.register("create_pdf", create_pdf, {
        "name": "create_pdf",
        "description": (
            "Create a PDF document - e.g. a handout, information sheet or report that shouldn't be edited - "
            "and send it to the user. It starts with the title; each entry in 'sections' adds an optional "
            "heading followed by its paragraphs and then its bullet points. **double asterisks** make text bold. "
            "To read an existing PDF, use read_document instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title, shown at the top"},
                "sections": {
                    "type": "array",
                    "description": "The document's sections, in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string", "description": "Optional section heading"},
                            "paragraphs": {"type": "array", "items": {"type": "string"}},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename, e.g. 'info_sheet.pdf'. Defaults to a name derived from the title.",
                },
            },
            "required": ["title", "sections"],
        },
    })
