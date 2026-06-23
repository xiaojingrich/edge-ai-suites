"""
Test script for the full grading pipeline — OCR → LLM grading → save results.
No MCP server or OpenClaw needed.

Usage:
    1. Start the LLM service (services/llm_serving/app.py) or set LLM_SERVICE_URL
    2. Put test homework images into: storage/smart-classroom/<SESSION_ID>/homework/
    3. Run: python test_grading_tools.py [SESSION_ID]

Default SESSION_ID: test-session
Default LLM URL: http://127.0.0.1:9905
"""

import os
import sys
import json
import re
import requests

SESSION_ID = sys.argv[1] if len(sys.argv) > 1 else "test-session"
LLM_URL = os.environ.get("LLM_SERVICE_URL", "http://127.0.0.1:9905")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from utils.runtime_config_loader import RuntimeConfig
from utils.config_loader import config

from mcp_server.tools._common import get_sessions_dir
from mcp_server.tools.grading_tools import (
    _get_homework_dir,
    _parse_student_from_filename,
    SUPPORTED_HOMEWORK_EXTENSIONS,
    CACHE_DIR,
    GRADING_DIR,
)


def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


_session = requests.Session()
_session.trust_env = False  # bypass system proxy for local requests


def check_llm_service():
    """Check if LLM service is running."""
    try:
        resp = _session.get(f"{LLM_URL}/health", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.ConnectionError:
        return False


def call_llm(prompt: str, max_tokens: int = 2048, temperature: float = 0.3) -> str:
    """Call LLM via OpenAI-compatible API."""
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = _session.post(f"{LLM_URL}/v1/chat/completions", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def build_grading_prompt(ocr_text: str, filename: str) -> str:
    """Build the grading prompt for the LLM."""
    return f"""你是一个专业的作业批改助手。请根据以下 OCR 识别出的学生作业内容进行评分。

## 作业文件: {filename}

## 学生作业内容（OCR 识别）:
```
{ocr_text}
```

## 评分要求:
1. 分析每道题的正确性
2. 判断这份作业是否有明确的分值标注（如"满分100分"、"每题10分"等）
   - 如果有分值：使用 scored 模式，给出具体得分
   - 如果没有分值：使用 correctness 模式，只判断对错
3. 给出具体的批改意见和总评

## 输出格式:
请严格输出以下 JSON 格式（不要输出其他内容）:

如果是有分值的（scored 模式）:
```json
{{
  "mode": "scored",
  "score": <得分>,
  "total": <总分>,
  "corrections": [
    {{
      "question": "题目标识（如：第1题）",
      "question_type": "choice|fill_in|short_answer|calculation|essay",
      "student_answer": "学生的答案",
      "correct_answer": "正确答案",
      "is_correct": true/false,
      "points_earned": <该题得分>,
      "points_possible": <该题满分>,
      "feedback": "具体反馈"
    }}
  ],
  "comments": "总体评价",
  "summary": "给教师的简短摘要"
}}
```

如果是没有分值的（correctness 模式）:
```json
{{
  "mode": "correctness",
  "correct_count": <正确数>,
  "total_count": <总题数>,
  "accuracy": "百分比",
  "corrections": [
    {{
      "question": "题目标识",
      "question_type": "choice|fill_in|short_answer|calculation|essay",
      "student_answer": "学生的答案",
      "correct_answer": "正确答案",
      "is_correct": true/false,
      "verdict": "correct|partial|incorrect",
      "feedback": "具体反馈"
    }}
  ],
  "comments": "总体评价",
  "summary": "给教师的简短摘要"
}}
```

请直接输出 JSON，不要加任何额外说明。"""


def parse_llm_json(text: str) -> dict:
    """Extract JSON from LLM response (handles markdown code blocks)."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1)

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def run_ocr(filename: str, homework_dir: str) -> str:
    """Run OCR on a single file, with caching."""
    file_path = os.path.join(homework_dir, filename)
    cache_dir = os.path.join(homework_dir, CACHE_DIR)
    os.makedirs(cache_dir, exist_ok=True)

    cache_name = os.path.splitext(filename)[0] + "_ocr.txt"
    cache_path = os.path.join(cache_dir, cache_name)

    if os.path.exists(cache_path):
        print(f"    [cache hit] {cache_name}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    print(f"    Running OCR on {filename}...")

    from components.ocr_component import OCRComponent

    provider = config.models.ocr.provider

    ocr = OCRComponent(
        session_id=SESSION_ID,
        provider=provider,
        lang=config.app.language,
        device=config.models.ocr.device,
    )

    ext = os.path.splitext(filename)[1].lower()

    if provider == "paddleocr-vl":
        if ext == ".pdf":
            from utils.ocr_utils.pdf_utils import pdf_to_images
            images = pdf_to_images(file_path, dpi=300)
            page_markdowns = []
            for i, img_path in enumerate(images, 1):
                result = ocr.ocr_model.extract_structured(img_path)
                page_markdowns.append(f"--- Page {i} ---\n{result.markdown}")
            extracted_text = "\n\n".join(page_markdowns)
        else:
            result = ocr.ocr_model.extract_structured(file_path)
            extracted_text = result.markdown
    elif ext == ".pdf":
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

    return extracted_text


def save_result(filename: str, ocr_text: str, result: dict, homework_dir: str):
    """Save grading result to grading/ directory."""
    grading_dir = os.path.join(homework_dir, GRADING_DIR)
    os.makedirs(grading_dir, exist_ok=True)

    student_info = _parse_student_from_filename(filename)
    student_name = student_info.get("student_name", "")
    student_id = student_info.get("student_id", "")

    results_path = os.path.join(grading_dir, "grading_results.json")
    existing = []
    if os.path.exists(results_path):
        with open(results_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing = [r for r in existing if r.get("filename") != filename]
    existing.append({
        "filename": filename,
        "student_name": student_name,
        "student_id": student_id,
        "ocr_text": ocr_text,
        "result": result,
    })
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    mode = result.get("mode", "scored")
    report_name = os.path.splitext(filename)[0] + "_grading.md"
    report_path = os.path.join(grading_dir, report_name)

    if mode == "scored":
        score_line = f"**得分**: {result.get('score', 'N/A')}/{result.get('total', 'N/A')}"
    else:
        score_line = f"**正确率**: {result.get('correct_count', 0)}/{result.get('total_count', 0)} ({result.get('accuracy', 'N/A')})"

    lines = ["# 作业批改报告", ""]
    lines.append(f"**文件**: {filename}")
    if student_name or student_id:
        parts = []
        if student_name:
            parts.append(student_name)
        if student_id:
            parts.append(f"学号 {student_id}")
        lines.append(f"**学生**: {' / '.join(parts)}")
    lines.append(score_line)
    lines.extend(["", "---", "", "## 学生作业内容（OCR 识别）", "", "```", ocr_text, "```", "", "---", "", "## 批改详情", ""])

    for i, c in enumerate(result.get("corrections", []), 1):
        is_correct = c.get("is_correct", False)
        verdict = c.get("verdict", "")
        if is_correct or verdict == "correct":
            mark = "✓"
        elif verdict == "partial":
            mark = "△"
        else:
            mark = "✗"
        lines.append(f"### {mark} {c.get('question', f'题目 {i}')}")
        lines.append("")
        lines.append(f"- **学生答案**: {c.get('student_answer', 'N/A')}")
        if not is_correct:
            lines.append(f"- **正确答案**: {c.get('correct_answer', 'N/A')}")
        if mode == "scored":
            lines.append(f"- **得分**: {c.get('points_earned', 0)}/{c.get('points_possible', 0)}")
        if c.get("feedback"):
            lines.append(f"- **反馈**: {c['feedback']}")
        lines.append("")

    lines.extend(["---", "", "## 总评", "", result.get("comments", ""), ""])
    if result.get("summary"):
        lines.extend(["## 教师摘要", "", result["summary"], ""])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return results_path, report_path


def main():
    print(f"{'='*60}")
    print(f"  Smart Classroom Grading Pipeline Test")
    print(f"{'='*60}")
    print(f"  Session:      {SESSION_ID}")
    print(f"  LLM URL:      {LLM_URL}")
    print(f"  OCR Provider: {config.models.ocr.provider}")
    print(f"  Language:     {config.app.language}")

    # 1. Check homework directory
    print_section("1. Check Homework Directory")
    homework_dir = _get_homework_dir(SESSION_ID)
    print(f"  Path: {homework_dir}")

    if not os.path.exists(homework_dir):
        os.makedirs(homework_dir, exist_ok=True)
        print(f"  [!] Created directory. Put test images here and re-run.")
        return

    files = [
        f for f in sorted(os.listdir(homework_dir))
        if os.path.isfile(os.path.join(homework_dir, f))
        and f.lower().endswith(SUPPORTED_HOMEWORK_EXTENSIONS)
    ]

    if not files:
        print(f"  [!] No homework files found. Put images/PDFs here:")
        print(f"      {homework_dir}")
        return

    print(f"  Found {len(files)} file(s):")
    for f in files:
        info = _parse_student_from_filename(f)
        name = info.get("student_name", "") or "(unknown)"
        print(f"    - {f}  →  {name}")

    # 2. Check LLM service
    print_section("2. Check LLM Service")
    if check_llm_service():
        print(f"  ✓ LLM service is running at {LLM_URL}")
    else:
        print(f"  ✗ LLM service not available at {LLM_URL}")
        print(f"  Start it with: python services/llm_serving/app.py")
        print(f"  Or set LLM_SERVICE_URL env var to a different endpoint.")
        return

    # 3. OCR
    print_section("3. OCR Extraction")
    ocr_results = {}
    for f in files:
        print(f"  [{f}]")
        try:
            text = run_ocr(f, homework_dir)
            ocr_results[f] = text
            preview = text[:200].replace("\n", " ")
            print(f"    → {len(text)} chars: {preview}...")
        except Exception as e:
            print(f"    → ERROR: {e}")
            ocr_results[f] = None

    # 4. LLM Grading
    print_section("4. LLM Grading")
    grading_results = {}
    for f, ocr_text in ocr_results.items():
        if not ocr_text:
            print(f"  [{f}] Skipped (no OCR text)")
            continue

        print(f"  [{f}] Sending to LLM for grading...")
        prompt = build_grading_prompt(ocr_text, f)

        try:
            response = call_llm(prompt)
            print(f"    LLM response ({len(response)} chars)")

            result = parse_llm_json(response)
            if result:
                mode = result.get("mode", "?")
                if mode == "scored":
                    print(f"    → Score: {result.get('score')}/{result.get('total')}")
                else:
                    print(f"    → Accuracy: {result.get('correct_count')}/{result.get('total_count')} ({result.get('accuracy')})")
                grading_results[f] = result
            else:
                print(f"    → [ERROR] Failed to parse JSON from LLM response:")
                print(f"      {response[:300]}")
                grading_results[f] = None
        except Exception as e:
            print(f"    → [ERROR] LLM call failed: {e}")
            grading_results[f] = None

    # 5. Save Results
    print_section("5. Save Results")
    for f, result in grading_results.items():
        if not result:
            continue
        results_path, report_path = save_result(f, ocr_results[f], result, homework_dir)
        print(f"  [{f}]")
        print(f"    JSON:   {results_path}")
        print(f"    Report: {report_path}")

    # 6. Summary
    print_section("Summary")
    graded = [f for f, r in grading_results.items() if r]
    failed = [f for f, r in grading_results.items() if r is None]

    print(f"  Total files:  {len(files)}")
    print(f"  OCR success:  {sum(1 for v in ocr_results.values() if v)}")
    print(f"  Graded:       {len(graded)}")
    if failed:
        print(f"  Failed:       {len(failed)} — {failed}")

    print(f"\n  Results saved to: {os.path.join(homework_dir, GRADING_DIR)}/")

    if graded:
        print(f"\n  --- Grade Table ---")
        print(f"  {'File':<25} {'Student':<10} {'Score':<15}")
        print(f"  {'-'*25} {'-'*10} {'-'*15}")
        for f in graded:
            r = grading_results[f]
            info = _parse_student_from_filename(f)
            name = info.get("student_name", "") or "—"
            if r["mode"] == "scored":
                score = f"{r.get('score')}/{r.get('total')}"
            else:
                score = f"{r.get('correct_count')}/{r.get('total_count')} ({r.get('accuracy')})"
            print(f"  {f:<25} {name:<10} {score:<15}")


if __name__ == "__main__":
    main()
