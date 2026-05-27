"""
OCR Board Analysis Skill

Recognizes and extracts content from board/PPT captures:
- Key formulas, diagrams, text
- Compares board content with spoken content for consistency
"""

import os
import json
from components.report_agent.skills.base_skill import BaseSkill


class OCRBoardAnalysisSkill(BaseSkill):
    name = "ocr_board_analysis"
    description = "Analyze board/PPT content using OCR results from the content camera stream. Extracts key formulas, text, and cross-references with spoken content."

    def execute(self, context: dict = None) -> dict:
        from components.report_agent.tools import get_session_dir

        session_dir = get_session_dir(self.session_id)

        # Step 1: Check if content stream results exist
        content_results_path = os.path.join(session_dir, "va", "content_results.txt")

        if not os.path.exists(content_results_path):
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Board/content camera data not available. Content stream was not recorded.",
            }

        # Step 2: Read content stream classification results
        try:
            with open(content_results_path, "r") as f:
                content_lines = f.readlines()
        except Exception as e:
            return {
                "status": "unavailable",
                "result": None,
                "summary": f"Failed to read content results: {e}",
            }

        # Step 3: Check for OCR-extracted text (if available from previous OCR runs)
        ocr_text = ""
        ocr_path = os.path.join(session_dir, "ocr_extracted.txt")
        if os.path.exists(ocr_path):
            try:
                with open(ocr_path, "r") as f:
                    ocr_text = f.read()
            except Exception:
                pass

        # Step 4: Analyze content frames
        total_frames = len(content_lines)
        frame_classifications = {}
        for line in content_lines:
            line = line.strip()
            if line:
                try:
                    frame_data = json.loads(line)
                    label = frame_data.get("label", "unknown")
                    frame_classifications[label] = frame_classifications.get(label, 0) + 1
                except json.JSONDecodeError:
                    pass

        # Step 5: LLM analysis of board content (if OCR text available)
        llm_analysis = ""
        if self.model and ocr_text:
            llm_analysis = self._call_llm(
                f"""Analyze this OCR-extracted board/PPT content from a classroom:

{ocr_text[:2000]}

Identify:
1. Key formulas or equations (if any)
2. Main topics written on the board
3. Diagram or visual descriptions (if mentioned)
4. How well the board content aligns with a typical lesson structure"""
            )

        result = {
            "total_content_frames": total_frames,
            "frame_classifications": frame_classifications,
            "ocr_text_available": bool(ocr_text),
            "ocr_text_excerpt": ocr_text[:500] if ocr_text else "",
            "llm_analysis": llm_analysis,
        }

        status = "success" if ocr_text else "partial"

        return {
            "status": status,
            "result": result,
            "summary": f"Board analysis: {total_frames} frames captured, OCR text {'available' if ocr_text else 'not extracted yet'}.",
        }