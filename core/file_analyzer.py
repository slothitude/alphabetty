"""File analyzer — Extract and analyze text from uploaded files."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_text(file_path: Path) -> str:
    """Extract text content from a file based on its extension."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz
        doc = fitz.open(str(file_path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text

    elif suffix in (".docx", ".doc"):
        import zipfile
        from bs4 import BeautifulSoup
        with zipfile.ZipFile(str(file_path)) as z:
            if "word/document.xml" in z.namelist():
                with z.open("word/document.xml") as f:
                    soup = BeautifulSoup(f, "lxml")
                    return soup.get_text(separator="\n")

    elif suffix in (".txt", ".md", ".py", ".js", ".ts", ".json", ".csv",
                    ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
                    ".rs", ".go", ".java", ".c", ".cpp", ".h"):
        return file_path.read_text(encoding="utf-8", errors="replace")

    return ""
