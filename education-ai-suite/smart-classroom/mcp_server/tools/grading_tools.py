"""Homework grading tools for the Smart Classroom MCP server."""

import os
import re
import json
import base64

from mcp_server.tools._common import get_sessions_dir


HOMEWORK_DIR = "homework"
CACHE_DIR = ".cache"
GRADING_DIR = "grading"
GRADING_RESULTS_FILE = "grading_results.json"

SUPPORTED_HOMEWORK_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp", ".pdf",
)

FILENAME_PATTERNS = [
    re.compile(r"^(\d{4,12})[_\-](.+?)(?:[_\-].+)?\.\w+$"),
    re.compile(r"^(.+?)[_\-](\d{4,12})(?:[_\-].+)?\.\w+$"),
    re.compile(r"^(\d{4,12})\.\w+$"),
    re.compile(r"^([^\d_\-][^_\-]*?)(?:[_\-].+)?\.\w+$"),
]


def _get_homework_dir(session_id: str) -> str:
    return os.path.join(get_sessions_dir(), session_id, HOMEWORK_DIR)


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


def _get_vlm_describe_fn():
    """Get VLM image description function if available and enabled.

    Returns None if VLM is disabled. When enabled, returns a callable
    that accepts a PIL Image and returns a text description.
    """
    from utils.config_loader import config as app_config

    vlm_config = getattr(app_config.models.ocr, "vlm_describe", None)
    if not vlm_config or not getattr(vlm_config, "enabled", False):
        return None

    server_url = getattr(vlm_config, "server_url", None)
    if not server_url:
        return None

    def describe(image):
        import io
        import requests

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        prompt = getattr(vlm_config, "prompt", "描述这张图片中的内容，特别是与学生作业答案相关的图形、图表或手绘内容。")
        payload = {
            "model": getattr(vlm_config, "model_name", "Qwen2.5-VL-3B-Instruct"),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": getattr(vlm_config, "max_tokens", 256),
        }
        resp = requests.post(f"{server_url}/v1/chat/completions", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return describe


def register_grading_tools(mcp):

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
        """Run OCR on a homework file (image or scanned PDF) to extract text.

        Supports two providers:
        - Traditional OCR (native/openvino): extracts plain text line by line.
        - PaddleOCR-VL (paddleocr-vl): extracts structured Markdown with layout
          detection. Also detects image regions (figures, charts) and optionally
          describes them via VLM if enabled in config.

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

        cache_dir = os.path.join(homework_dir, CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)
        cache_name = os.path.splitext(filename)[0] + "_ocr.txt"
        cache_path = os.path.join(cache_dir, cache_name)
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_text = f.read()
            meta_cache_path = os.path.splitext(cache_path)[0] + "_meta.json"
            has_images = False
            image_blocks = []
            if os.path.exists(meta_cache_path):
                with open(meta_cache_path, "r", encoding="utf-8") as f:
                    meta = json.loads(f.read())
                    has_images = meta.get("has_images", False)
                    image_blocks = meta.get("image_blocks", [])
            return {
                "session_id": session_id,
                "filename": filename,
                "ocr_text": cached_text,
                "has_images": has_images,
                "image_blocks": image_blocks,
                "cached": True,
            }

        try:
            from components.ocr_component import OCRComponent
            from utils.config_loader import config as app_config

            provider = app_config.models.ocr.provider
            vlm_enabled = False
            vlm_describe_fn = None

            if provider == "paddleocr-vl":
                vlm_describe_fn = _get_vlm_describe_fn()
                vlm_enabled = vlm_describe_fn is not None

            ocr = OCRComponent(
                session_id=session_id,
                provider=provider,
                lang=app_config.app.language,
                device=app_config.models.ocr.device,
                vlm_enabled=vlm_enabled,
                vlm_describe_fn=vlm_describe_fn,
            )

            ext = os.path.splitext(filename)[1].lower()
            has_images = False
            image_blocks = []

            if provider == "paddleocr-vl":
                if ext == ".pdf":
                    from utils.ocr_utils.pdf_utils import pdf_to_images
                    images = pdf_to_images(file_path, dpi=300)
                    page_markdowns = []
                    for i, img_path in enumerate(images, 1):
                        result = ocr.ocr_model.extract_structured(img_path)
                        page_markdowns.append(f"--- Page {i} ---\n{result.markdown}")
                        if result.has_images:
                            has_images = True
                        for block in result.image_blocks:
                            image_blocks.append({
                                "page": i,
                                "index": block.index,
                                "coordinate": block.coordinate,
                                "label": block.label,
                                "score": block.score,
                                "description": block.description,
                            })
                    extracted_text = "\n\n".join(page_markdowns)
                else:
                    result = ocr.ocr_model.extract_structured(file_path)
                    extracted_text = result.markdown
                    has_images = result.has_images
                    for block in result.image_blocks:
                        image_blocks.append({
                            "index": block.index,
                            "coordinate": block.coordinate,
                            "label": block.label,
                            "score": block.score,
                            "description": block.description,
                        })
            else:
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

            if provider == "paddleocr-vl":
                meta_cache_path = os.path.splitext(cache_path)[0] + "_meta.json"
                with open(meta_cache_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "has_images": has_images,
                        "image_blocks": image_blocks,
                    }, ensure_ascii=False))

            return {
                "session_id": session_id,
                "filename": filename,
                "ocr_text": extracted_text,
                "has_images": has_images,
                "image_blocks": image_blocks,
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

        When using paddleocr-vl provider, also detects image regions and returns
        has_images flag per file to indicate which submissions need visual review.

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

        from utils.config_loader import config as app_config
        provider = app_config.models.ocr.provider

        vlm_enabled = False
        vlm_describe_fn = None
        if provider == "paddleocr-vl":
            vlm_describe_fn = _get_vlm_describe_fn()
            vlm_enabled = vlm_describe_fn is not None

        cache_dir = os.path.join(homework_dir, CACHE_DIR)
        os.makedirs(cache_dir, exist_ok=True)

        results = []
        for entry in sorted(os.listdir(homework_dir)):
            file_path = os.path.join(homework_dir, entry)
            if not os.path.isfile(file_path):
                continue
            if not entry.lower().endswith(SUPPORTED_HOMEWORK_EXTENSIONS):
                continue

            cache_name = os.path.splitext(entry)[0] + "_ocr.txt"
            cache_path = os.path.join(cache_dir, cache_name)

            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    ocr_text = f.read()
                has_images = False
                image_blocks = []
                meta_cache_path = os.path.splitext(cache_path)[0] + "_meta.json"
                if os.path.exists(meta_cache_path):
                    with open(meta_cache_path, "r", encoding="utf-8") as f:
                        meta = json.loads(f.read())
                        has_images = meta.get("has_images", False)
                        image_blocks = meta.get("image_blocks", [])
                results.append({
                    "filename": entry,
                    "ocr_text": ocr_text,
                    "has_images": has_images,
                    "image_blocks": image_blocks,
                    "cached": True,
                    "status": "success",
                })
                continue

            try:
                from components.ocr_component import OCRComponent

                ocr = OCRComponent(
                    session_id=session_id,
                    provider=provider,
                    lang=app_config.app.language,
                    device=app_config.models.ocr.device,
                    vlm_enabled=vlm_enabled,
                    vlm_describe_fn=vlm_describe_fn,
                )

                ext = os.path.splitext(entry)[1].lower()
                has_images = False
                image_blocks = []

                if provider == "paddleocr-vl":
                    if ext == ".pdf":
                        from utils.ocr_utils.pdf_utils import pdf_to_images
                        images = pdf_to_images(file_path, dpi=300)
                        page_markdowns = []
                        for i, img_path in enumerate(images, 1):
                            result = ocr.ocr_model.extract_structured(img_path)
                            page_markdowns.append(f"--- Page {i} ---\n{result.markdown}")
                            if result.has_images:
                                has_images = True
                            for block in result.image_blocks:
                                image_blocks.append({
                                    "page": i,
                                    "index": block.index,
                                    "coordinate": block.coordinate,
                                    "label": block.label,
                                    "score": block.score,
                                    "description": block.description,
                                })
                        extracted_text = "\n\n".join(page_markdowns)
                    else:
                        result = ocr.ocr_model.extract_structured(file_path)
                        extracted_text = result.markdown
                        has_images = result.has_images
                        for block in result.image_blocks:
                            image_blocks.append({
                                "index": block.index,
                                "coordinate": block.coordinate,
                                "label": block.label,
                                "score": block.score,
                                "description": block.description,
                            })
                else:
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

                if provider == "paddleocr-vl":
                    meta_cache_path = os.path.splitext(cache_path)[0] + "_meta.json"
                    with open(meta_cache_path, "w", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "has_images": has_images,
                            "image_blocks": image_blocks,
                        }, ensure_ascii=False))

                results.append({
                    "filename": entry,
                    "ocr_text": extracted_text,
                    "has_images": has_images,
                    "image_blocks": image_blocks,
                    "cached": False,
                    "status": "success",
                })
            except Exception as e:
                results.append({
                    "filename": entry,
                    "ocr_text": "",
                    "has_images": False,
                    "image_blocks": [],
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
        grading_dir = os.path.join(homework_dir, GRADING_DIR)
        os.makedirs(grading_dir, exist_ok=True)

        results_path = os.path.join(grading_dir, GRADING_RESULTS_FILE)

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

        report_name = os.path.splitext(filename)[0] + "_grading.md"
        report_path = os.path.join(grading_dir, report_name)

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
        lines.extend([
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
        ])

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
        results_path = os.path.join(homework_dir, GRADING_DIR, GRADING_RESULTS_FILE)

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
