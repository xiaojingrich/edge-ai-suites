"""
Smart Classroom MCP Server

Exposes classroom session data and tools via the MCP protocol.
This is the unified interface layer for all smart-classroom capabilities
that external agents need to access.

Started from main.py alongside the main application.
"""

import os
import re
import json
import base64

from mcp.server.fastmcp import FastMCP
from utils.runtime_config_loader import RuntimeConfig


mcp = FastMCP(
    "smart-classroom",
    description="Smart Classroom data and tools for classroom evaluation and analysis",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_SERVER_PORT", "8100")),
)


def _get_sessions_dir() -> str:
    project_config = RuntimeConfig.get_section("Project")
    return os.path.join(
        project_config.get("location", "storage"),
        project_config.get("name", "smart-classroom"),
    )


# ============================================================
# Data Tools — read session files
# ============================================================


IGNORED_DIRS = {"audio"}

# Compact files used to build a report. Safe to read together.
REPORT_FILES = (
    "summary.md",
    "mindmap.mmd",
    "topics.json",
    "va/class_statistics.json",
)

# Full raw transcripts. Large — only for detail Q&A, never for a report.
TRANSCRIPT_FILES = (
    "transcription.txt",
    "teacher_transcription.txt",
    "content_segmentation_transcription.txt",
)


@mcp.tool()
def list_sessions() -> dict:
    """List all available classroom sessions, with each session's files grouped by purpose.

    report_files: compact files for generating a report (read these for a report).
    transcript_files: large raw transcripts — only for answering questions about the
    actual spoken words; do NOT load them when generating a report (use get_teaching_stats
    for transcript-derived numbers instead).
    """
    sessions_dir = _get_sessions_dir()
    if not os.path.exists(sessions_dir):
        return {"sessions": [], "error": f"Sessions directory not found: {sessions_dir}"}

    sessions = []
    for entry in sorted(os.listdir(sessions_dir), reverse=True):
        session_path = os.path.join(sessions_dir, entry)
        if not os.path.isdir(session_path) or entry.startswith(".") or entry in IGNORED_DIRS:
            continue

        report_files = [
            rel for rel in REPORT_FILES
            if os.path.isfile(os.path.join(session_path, rel))
        ]
        transcript_files = [
            rel for rel in TRANSCRIPT_FILES
            if os.path.isfile(os.path.join(session_path, rel))
        ]

        sessions.append({
            "session_id": entry,
            "report_files": report_files,
            "transcript_files": transcript_files,
            "note": "For a report, read report_files only. transcript_files are large and for detail Q&A only.",
        })

    return {"sessions": sessions}


@mcp.tool()
def read_session_files(session_id: str, filenames: list[str]) -> dict:
    """Read one or more data files from a classroom session.

    Args:
        session_id: The session identifier (directory name).
        filenames: List of file paths relative to session directory (e.g., ["transcription.txt", "va/class_statistics.json"]).
    """
    session_dir = os.path.join(_get_sessions_dir(), session_id)
    results = {}

    for filename in filenames:
        file_path = os.path.join(session_dir, filename)

        normalized = os.path.normpath(file_path)
        if not normalized.startswith(os.path.normpath(session_dir)):
            results[filename] = {"error": "path traversal not allowed"}
            continue

        if not os.path.exists(file_path):
            results[filename] = {"error": "file not found"}
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                results[filename] = f.read()
        except Exception as e:
            results[filename] = {"error": str(e)}

    return {"session_id": session_id, "files": results}


# ============================================================
# Analytics Tools — pre-computed statistics
# ============================================================


@mcp.tool()
def get_teaching_stats(session_id: str) -> dict:
    """Get pre-computed teaching statistics for a session.

    Returns teacher speaking duration, speed, question count, class duration,
    and student engagement metrics. Use this instead of reading raw transcription
    files when you need numerical statistics.

    Args:
        session_id: The session identifier (directory name).
    """
    session_dir = os.path.join(_get_sessions_dir(), session_id)
    stats = {}

    # --- Teacher stats from teacher_transcription.txt ---
    teacher_path = os.path.join(session_dir, "teacher_transcription.txt")
    if os.path.exists(teacher_path):
        with open(teacher_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.read().strip().split("\n") if l.strip()]

        teacher_speaking_sec = 0
        teacher_chars = 0
        teacher_question_count = 0

        for line in lines:
            match = re.match(r"\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]\s*(.*)", line)
            if not match:
                continue
            start = float(match.group(1))
            end = float(match.group(2))
            text = match.group(3)
            teacher_speaking_sec += (end - start)
            teacher_chars += len(text)
            if text.endswith("？") or text.endswith("?"):
                teacher_question_count += 1

        teacher_speaking_min = teacher_speaking_sec / 60.0
        speaking_speed = round(teacher_chars / teacher_speaking_min) if teacher_speaking_min > 0 else 0

        stats["teacher_speaking_duration_sec"] = round(teacher_speaking_sec)
        stats["teacher_speaking_duration_min"] = round(teacher_speaking_min, 1)
        stats["teacher_total_chars"] = teacher_chars
        stats["teacher_speaking_speed_chars_per_min"] = speaking_speed
        stats["teacher_question_count"] = teacher_question_count
        stats["teacher_sentence_count"] = len(lines)

    # --- Class duration from content_segmentation_transcription.txt ---
    cs_path = os.path.join(session_dir, "content_segmentation_transcription.txt")
    if os.path.exists(cs_path):
        with open(cs_path, "r", encoding="utf-8") as f:
            cs_lines = f.read().strip().split("\n")

        last_end = 0
        for line in reversed(cs_lines):
            match = re.match(r"\[(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\]", line.strip())
            if match:
                last_end = float(match.group(2))
                break

        stats["class_duration_sec"] = round(last_end)
        stats["class_duration_min"] = round(last_end / 60.0, 1)

        if "teacher_speaking_duration_sec" in stats and last_end > 0:
            stats["teacher_speaking_ratio"] = round(
                stats["teacher_speaking_duration_sec"] / last_end * 100, 1
            )

    if not stats:
        return {"error": "No data files found for this session", "session_id": session_id}

    return {"session_id": session_id, "stats": stats}


# ============================================================
# Grading Tools — homework submission management and OCR
# ============================================================

HOMEWORK_DIR = "homework"
GRADING_RESULTS_FILE = "grading_results.json"


def _get_homework_dir(session_id: str) -> str:
    return os.path.join(_get_sessions_dir(), session_id, HOMEWORK_DIR)


SUPPORTED_HOMEWORK_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp", ".pdf",
)

# Common filename patterns for student identification:
#   张三.pdf, 李四_作业.jpg, 2024001_王五.pdf, 2024001.pdf, 张三-20240315.pdf
FILENAME_PATTERNS = [
    # student_id + separator + name: "2024001_张三.pdf", "2024001-李四.jpg"
    re.compile(r"^(\d{4,12})[_\-](.+?)(?:[_\-].+)?\.\w+$"),
    # name + separator + student_id: "张三_2024001.pdf"
    re.compile(r"^(.+?)[_\-](\d{4,12})(?:[_\-].+)?\.\w+$"),
    # student_id only: "2024001.pdf"
    re.compile(r"^(\d{4,12})\.\w+$"),
    # name only (non-numeric): "张三.pdf", "张三_作业.pdf"
    re.compile(r"^([^\d_\-][^_\-]*?)(?:[_\-].+)?\.\w+$"),
]


def _parse_student_from_filename(filename: str) -> dict:
    """Try to extract student name/id from filename."""
    for i, pattern in enumerate(FILENAME_PATTERNS):
        match = pattern.match(filename)
        if not match:
            continue
        groups = match.groups()
        if i == 0:
            return {"student_id": groups[0], "student_name": groups[1]}
        elif i == 1:
            return {"student_name": groups[0], "student_id": groups[1]}
        elif i == 2:
            return {"student_id": groups[0], "student_name": ""}
        elif i == 3:
            return {"student_name": groups[0], "student_id": ""}
    return {"student_name": "", "student_id": ""}


@mcp.tool()
def list_homework_submissions(session_id: str) -> dict:
    """List all homework submissions (images or scanned PDFs) for a session.

    Returns a list of submitted homework files with metadata and parsed student
    identity from the filename (if the filename contains student name or ID).

    Args:
        session_id: The session identifier (directory name).
    """
    homework_dir = _get_homework_dir(session_id)
    if not os.path.exists(homework_dir):
        return {
            "session_id": session_id,
            "submissions": [],
            "note": "No homework directory found. Students have not submitted homework for this session yet.",
        }

    submissions = []

    for entry in sorted(os.listdir(homework_dir)):
        file_path = os.path.join(homework_dir, entry)
        if not os.path.isfile(file_path):
            continue
        if not entry.lower().endswith(SUPPORTED_HOMEWORK_EXTENSIONS):
            continue

        stat = os.stat(file_path)
        file_type = "pdf" if entry.lower().endswith(".pdf") else "image"
        student_info = _parse_student_from_filename(entry)

        submissions.append({
            "filename": entry,
            "file_type": file_type,
            "size_bytes": stat.st_size,
            "submitted_at": stat.st_mtime,
            "student_name": student_info.get("student_name", ""),
            "student_id": student_info.get("student_id", ""),
        })

    return {
        "session_id": session_id,
        "homework_dir": homework_dir,
        "submissions": submissions,
        "total_count": len(submissions),
    }


@mcp.tool()
def read_homework_image(session_id: str, filename: str, page: int = 1) -> dict:
    """Read a homework file and return it as base64-encoded image data.

    For image files, returns the image directly.
    For PDF files (e.g., scanned documents from a document camera), converts the
    specified page to an image and returns it.

    Args:
        session_id: The session identifier (directory name).
        filename: The filename within the homework directory (image or PDF).
        page: Page number to read for PDF files (1-based, default 1).
    """
    homework_dir = _get_homework_dir(session_id)
    file_path = os.path.join(homework_dir, filename)

    normalized = os.path.normpath(file_path)
    if not normalized.startswith(os.path.normpath(homework_dir)):
        return {"error": "path traversal not allowed"}

    if not os.path.exists(file_path):
        return {"error": f"File not found: {filename}"}

    try:
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".pdf":
            from utils.ocr_utils.pdf_utils import pdf_to_images
            images = pdf_to_images(file_path, dpi=300)
            if page < 1 or page > len(images):
                return {"error": f"Page {page} out of range (1-{len(images)})"}
            img_path = images[page - 1]
            with open(img_path, "rb") as f:
                image_data = f.read()
            return {
                "session_id": session_id,
                "filename": filename,
                "page": page,
                "total_pages": len(images),
                "mime_type": "image/png",
                "image_base64": base64.b64encode(image_data).decode("utf-8"),
                "size_bytes": len(image_data),
            }
        else:
            with open(file_path, "rb") as f:
                image_data = f.read()
            mime_map = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".bmp": "image/bmp",
                ".tiff": "image/tiff", ".webp": "image/webp",
            }
            mime_type = mime_map.get(ext, "image/jpeg")
            return {
                "session_id": session_id,
                "filename": filename,
                "mime_type": mime_type,
                "image_base64": base64.b64encode(image_data).decode("utf-8"),
                "size_bytes": len(image_data),
            }
    except Exception as e:
        return {"error": f"Failed to read file: {str(e)}"}


@mcp.tool()
def ocr_homework(session_id: str, filename: str) -> dict:
    """Run OCR on a homework file (image or scanned PDF) to extract handwritten text.

    For images: processes the single image through OCR.
    For PDFs: converts all pages to images, runs OCR on each page, and combines results.
    The OCR result is cached in the homework directory for future use.

    Args:
        session_id: The session identifier (directory name).
        filename: The filename within the homework directory (image or PDF).
    """
    homework_dir = _get_homework_dir(session_id)
    file_path = os.path.join(homework_dir, filename)

    normalized = os.path.normpath(file_path)
    if not normalized.startswith(os.path.normpath(homework_dir)):
        return {"error": "path traversal not allowed"}

    if not os.path.exists(file_path):
        return {"error": f"File not found: {filename}"}

    cache_name = os.path.splitext(filename)[0] + "_ocr.txt"
    cache_path = os.path.join(homework_dir, cache_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached_text = f.read()
        return {
            "session_id": session_id,
            "filename": filename,
            "ocr_text": cached_text,
            "cached": True,
        }

    try:
        from components.ocr_component import OCRComponent
        from utils.config_loader import config as app_config

        ocr = OCRComponent(
            session_id=session_id,
            provider=app_config.models.ocr.provider,
            lang=app_config.app.language,
            device=app_config.models.ocr.device,
        )

        ext = os.path.splitext(filename)[1].lower()
        if ext == ".pdf":
            from utils.ocr_utils.pdf_utils import pdf_to_images
            images = pdf_to_images(file_path, dpi=300)
            page_texts = []
            for i, img_path in enumerate(images, 1):
                text = ocr.ocr_model.extract_text(img_path)
                page_texts.append(f"--- Page {i} ---\n{text}")
            extracted_text = "\n\n".join(page_texts)
        else:
            extracted_text = ocr.ocr_model.extract_text(file_path)

        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(extracted_text)

        return {
            "session_id": session_id,
            "filename": filename,
            "ocr_text": extracted_text,
            "cached": False,
        }
    except Exception as e:
        return {"error": f"OCR failed: {str(e)}"}


@mcp.tool()
def batch_ocr_homework(session_id: str) -> dict:
    """Run OCR on all homework submissions in a session at once.

    Processes all image and PDF files in the homework directory, returning
    the extracted text for each. Results are cached — previously OCR'd files
    return instantly from cache.

    Args:
        session_id: The session identifier (directory name).
    """
    homework_dir = _get_homework_dir(session_id)
    if not os.path.exists(homework_dir):
        return {
            "session_id": session_id,
            "results": [],
            "note": "No homework directory found.",
        }

    results = []
    for entry in sorted(os.listdir(homework_dir)):
        file_path = os.path.join(homework_dir, entry)
        if not os.path.isfile(file_path):
            continue
        if not entry.lower().endswith(SUPPORTED_HOMEWORK_EXTENSIONS):
            continue

        cache_name = os.path.splitext(entry)[0] + "_ocr.txt"
        cache_path = os.path.join(homework_dir, cache_name)

        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                ocr_text = f.read()
            results.append({
                "filename": entry,
                "ocr_text": ocr_text,
                "cached": True,
                "status": "success",
            })
            continue

        try:
            from components.ocr_component import OCRComponent
            from utils.config_loader import config as app_config

            ocr = OCRComponent(
                session_id=session_id,
                provider=app_config.models.ocr.provider,
                lang=app_config.app.language,
                device=app_config.models.ocr.device,
            )

            ext = os.path.splitext(entry)[1].lower()
            if ext == ".pdf":
                from utils.ocr_utils.pdf_utils import pdf_to_images
                images = pdf_to_images(file_path, dpi=300)
                page_texts = []
                for i, img_path in enumerate(images, 1):
                    text = ocr.ocr_model.extract_text(img_path)
                    page_texts.append(f"--- Page {i} ---\n{text}")
                extracted_text = "\n\n".join(page_texts)
            else:
                extracted_text = ocr.ocr_model.extract_text(file_path)

            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(extracted_text)

            results.append({
                "filename": entry,
                "ocr_text": extracted_text,
                "cached": False,
                "status": "success",
            })
        except Exception as e:
            results.append({
                "filename": entry,
                "ocr_text": "",
                "cached": False,
                "status": "failed",
                "error": str(e),
            })

    return {
        "session_id": session_id,
        "results": results,
        "total_count": len(results),
        "success_count": sum(1 for r in results if r["status"] == "success"),
    }


@mcp.tool()
def save_grading_result(
    session_id: str,
    filename: str,
    ocr_text: str,
    result: dict,
    student_name: str = "",
    student_id: str = "",
) -> dict:
    """Save the grading result for a homework submission.

    Persists both the scanned homework content (OCR text) and the grading feedback
    into a JSON summary and a human-readable Markdown report.

    Args:
        session_id: The session identifier (directory name).
        filename: The homework filename that was graded.
        ocr_text: The OCR-extracted text from the student's homework.
        result: Grading result dict with keys: 'score'/'correct_count', 'total'/'total_count', 'corrections' (list), 'comments', 'summary'.
        student_name: Student name (from filename or extracted from OCR content).
        student_id: Student ID/number (from filename or extracted from OCR content).
    """
    homework_dir = _get_homework_dir(session_id)
    if not os.path.exists(homework_dir):
        os.makedirs(homework_dir, exist_ok=True)

    # --- Save to JSON (structured, machine-readable) ---
    results_path = os.path.join(homework_dir, GRADING_RESULTS_FILE)

    existing_results = []
    if os.path.exists(results_path):
        try:
            with open(results_path, "r", encoding="utf-8") as f:
                existing_results = json.load(f)
        except (json.JSONDecodeError, Exception):
            existing_results = []

    existing_results = [r for r in existing_results if r.get("filename") != filename]

    grading_entry = {
        "filename": filename,
        "student_name": student_name,
        "student_id": student_id,
        "ocr_text": ocr_text,
        "result": result,
    }
    existing_results.append(grading_entry)

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(existing_results, f, ensure_ascii=False, indent=2)

    # --- Generate Markdown report (human-readable) ---
    report_name = os.path.splitext(filename)[0] + "_grading.md"
    report_path = os.path.join(homework_dir, report_name)

    mode = result.get("mode", "scored")
    comments = result.get("comments", "")
    summary = result.get("summary", "")
    corrections = result.get("corrections", [])

    if mode == "scored":
        score_line = f"**得分**: {result.get('score', 'N/A')}/{result.get('total', 'N/A')}"
    else:
        score_line = f"**正确率**: {result.get('correct_count', 0)}/{result.get('total_count', 0)} ({result.get('accuracy', 'N/A')})"

    student_line = ""
    if student_name or student_id:
        parts = []
        if student_name:
            parts.append(student_name)
        if student_id:
            parts.append(f"学号 {student_id}")
        student_line = f"**学生**: {' / '.join(parts)}"

    lines = [
        f"# 作业批改报告",
        f"",
        f"**文件**: {filename}",
    ]
    if student_line:
        lines.append(student_line)
    lines.append(score_line)
        f"",
        f"---",
        f"",
        f"## 学生作业内容（OCR 识别）",
        f"",
        f"```",
        ocr_text,
        f"```",
        f"",
        f"---",
        f"",
        f"## 批改详情",
        f"",
    ]

    if corrections:
        for i, c in enumerate(corrections, 1):
            is_correct = c.get("is_correct", False)
            verdict = c.get("verdict", "")
            if is_correct or verdict == "correct":
                mark = "✓"
            elif verdict == "partial":
                mark = "△"
            else:
                mark = "✗"
            lines.append(f"### {mark} {c.get('question', f'题目 {i}')}")
            lines.append(f"")
            lines.append(f"- **学生答案**: {c.get('student_answer', 'N/A')}")
            if not is_correct:
                lines.append(f"- **正确答案**: {c.get('correct_answer', 'N/A')}")
            if mode == "scored":
                lines.append(f"- **得分**: {c.get('points_earned', 0)}/{c.get('points_possible', 0)}")
            if c.get("feedback"):
                lines.append(f"- **反馈**: {c['feedback']}")
            lines.append(f"")

    lines.extend([
        f"---",
        f"",
        f"## 总评",
        f"",
        f"{comments}",
        f"",
    ])

    if summary:
        lines.extend([
            f"## 教师摘要",
            f"",
            f"{summary}",
            f"",
        ])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "session_id": session_id,
        "filename": filename,
        "saved": True,
        "results_file": results_path,
        "report_file": report_path,
    }


@mcp.tool()
def get_grading_results(session_id: str) -> dict:
    """Get all saved grading results for a session.

    Returns previously saved grading results for all homework submissions
    in the session.

    Args:
        session_id: The session identifier (directory name).
    """
    homework_dir = _get_homework_dir(session_id)
    results_path = os.path.join(homework_dir, GRADING_RESULTS_FILE)

    if not os.path.exists(results_path):
        return {
            "session_id": session_id,
            "results": [],
            "note": "No grading results found for this session.",
        }

    try:
        with open(results_path, "r", encoding="utf-8") as f:
            results = json.load(f)
        return {
            "session_id": session_id,
            "results": results,
            "total_graded": len(results),
        }
    except Exception as e:
        return {"error": f"Failed to read grading results: {str(e)}"}
