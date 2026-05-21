import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, Depends
from pydantic import BaseModel

from config import settings
from core.auth import get_current_user
from core.llm import call_llm
from models.user import User

router = APIRouter(tags=["files"])
logger = logging.getLogger(__name__)


async def extract_text_from_file(file_path: Path, filename: str) -> str:
    """Extract text content from uploaded file."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(str(file_path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text

    elif suffix in (".docx", ".doc"):
        # Basic DOCX extraction using zipfile
        import zipfile
        with zipfile.ZipFile(str(file_path)) as z:
            if "word/document.xml" in z.namelist():
                from bs4 import BeautifulSoup
                with z.open("word/document.xml") as f:
                    soup = BeautifulSoup(f, "lxml")
                    return soup.get_text(separator="\n")

    elif suffix in (".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".xml", ".yaml", ".yml"):
        return file_path.read_text(encoding="utf-8", errors="replace")

    elif suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return f"[Image file: {filename}]"

    return f"[Unsupported file type: {suffix}]"


@router.post("/files/upload")
async def upload_file(file: UploadFile = File(...), query: str = Form(None), user: User = Depends(get_current_user)):
    """Upload a file and optionally analyze it."""
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    result = {
        "filename": file.filename,
        "size": len(content),
        "path": str(file_path),
    }

    if query:
        text = await extract_text_from_file(file_path, file.filename)
        if text and not text.startswith("[Unsupported"):
            messages = [
                {"role": "system", "content": "You are analyzing a file. Answer the user's question based on the file content."},
                {"role": "user", "content": f"File: {file.filename}\n\nContent:\n{text[:50000]}\n\nQuestion: {query}"},
            ]
            analysis = await call_llm(messages)
            result["analysis"] = analysis
        else:
            result["analysis"] = text

    return result


@router.post("/files/analyze")
async def analyze_file(filename: str, query: str, user: User = Depends(get_current_user)):
    """Analyze a previously uploaded file."""
    file_path = Path(settings.upload_dir) / filename
    if not file_path.exists():
        return {"error": "File not found"}

    text = await extract_text_from_file(file_path, filename)
    messages = [
        {"role": "system", "content": "You are analyzing a file. Answer the user's question based on the file content."},
        {"role": "user", "content": f"File: {filename}\n\nContent:\n{text[:50000]}\n\nQuestion: {query}"},
    ]
    analysis = await call_llm(messages)
    return {"analysis": analysis}
