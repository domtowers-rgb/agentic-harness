from plugins.pdf_document import create_pdf
from plugins.powerpoint import create_presentation
from plugins.word_document import create_document

FORMATS = ("docx", "pdf", "pptx")


def create_file(format: str, title: str, sections: list, filename: str = None):
    """One tool for every document type, rather than one per format: every
    tool's definition is sent with every prompt, and the three separate
    ones had near-identical parameters - this saves ~450 prompt tokens.
    Sections map onto slides for pptx (heading = slide title; paragraphs
    then bullets = its bullet points)."""
    format = (format or "").lower().lstrip(".")
    if format == "docx":
        return create_document(title, sections, filename)
    if format == "pdf":
        return create_pdf(title, sections, filename)
    if format == "pptx":
        if not isinstance(sections, list):
            return {"error": "sections must be a list of {heading, paragraphs, bullets} objects"}
        slides = []
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                return {"error": f"section {i} must be an object with optional 'heading', 'paragraphs' and 'bullets'"}
            points = []
            for key in ("paragraphs", "bullets"):
                value = section.get(key) or []
                points += [value] if isinstance(value, str) else list(value)
            slides.append({"title": section.get("heading") or "", "bullets": points})
        return create_presentation(title, slides, filename=filename)
    return {"error": f"format must be one of: {', '.join(FORMATS)}"}


def register(registry):
    registry.register("create_file", create_file, {
        "name": "create_file",
        "description": (
            "Create a Word (docx), PDF or PowerPoint (pptx) file and send it to the user. "
            "It starts with the title; each section adds an optional heading, then paragraphs, then bullets "
            "(for pptx, each section is a slide). **bold** works in docx and pdf."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": list(FORMATS)},
                "title": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "paragraphs": {"type": "array", "items": {"type": "string"}},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "filename": {"type": "string", "description": "Optional"},
            },
            "required": ["format", "title", "sections"],
        },
    })
