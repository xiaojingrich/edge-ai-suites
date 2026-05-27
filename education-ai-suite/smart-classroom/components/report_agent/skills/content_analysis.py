"""
Content Analysis Skill

Combines transcription + summary + mindmap to extract:
- Teaching objective completion degree
- Knowledge point coverage
- Content structure quality assessment
"""

import json
from components.report_agent.skills.base_skill import BaseSkill


class ContentAnalysisSkill(BaseSkill):
    name = "content_analysis"
    description = "Analyze teaching content by combining transcription, summary, and mindmap. Extracts teaching objectives, knowledge coverage, and content structure quality."

    def execute(self, context: dict = None) -> dict:
        # Step 1: Collect all content data
        summary_raw = self.tools.execute_tool("get_class_summary")
        mindmap_raw = self.tools.execute_tool("get_mindmap")
        topics_raw = self.tools.execute_tool("get_topic_segmentation")

        has_summary = "NOT available" not in summary_raw and "empty" not in summary_raw
        has_mindmap = "NOT available" not in mindmap_raw and "empty" not in mindmap_raw
        has_topics = "NOT available" not in topics_raw and "empty" not in topics_raw

        if not has_summary and not has_mindmap:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Neither summary nor mindmap available for content analysis.",
            }

        # Step 2: Extract summary content
        summary_text = ""
        if has_summary:
            lines = summary_raw.split("\n")
            summary_text = "\n".join(lines[1:]) if len(lines) > 1 else summary_raw

        # Step 3: Count knowledge points from mindmap
        knowledge_points = 0
        if has_mindmap:
            try:
                mindmap_text = mindmap_raw.split("\n", 1)[1] if "\n" in mindmap_raw else mindmap_raw
                mindmap_data = json.loads(mindmap_text)
                knowledge_points = self._count_nodes(mindmap_data)
            except (json.JSONDecodeError, IndexError):
                knowledge_points = 0

        # Step 4: Count topics covered
        topics_count = 0
        if has_topics:
            try:
                topics_text = topics_raw.split("\n", 1)[1] if "\n" in topics_raw else topics_raw
                topics_data = json.loads(topics_text)
                topics_count = len(topics_data) if isinstance(topics_data, list) else 0
            except (json.JSONDecodeError, IndexError):
                topics_count = 0

        # Step 5: LLM-powered content quality analysis
        llm_analysis = ""
        if self.model and summary_text:
            llm_analysis = self._call_llm(
                f"""Analyze this class content summary for teaching quality. Provide a structured assessment:

1. Main teaching objectives identified (list them)
2. Content structure quality (Well-structured / Moderate / Fragmented)
3. Key concepts coverage breadth (Comprehensive / Adequate / Limited)
4. Suggestions for improvement (1-2 points)

Summary:
{summary_text[:2000]}

Knowledge points in mindmap: {knowledge_points}
Topic segments: {topics_count}"""
            )

        result = {
            "has_summary": has_summary,
            "has_mindmap": has_mindmap,
            "has_topics": has_topics,
            "knowledge_points_count": knowledge_points,
            "topics_count": topics_count,
            "summary_excerpt": summary_text[:500] if summary_text else "",
            "llm_analysis": llm_analysis,
        }

        return {
            "status": "success" if has_summary else "partial",
            "result": result,
            "summary": f"Content analysis: {knowledge_points} knowledge points, {topics_count} topics covered.",
        }

    def _count_nodes(self, data: dict, depth: int = 0) -> int:
        """Recursively count nodes in mindmap structure."""
        count = 0
        if isinstance(data, dict):
            if "topic" in data or "id" in data:
                count = 1
            children = data.get("children", [])
            if isinstance(children, list):
                for child in children:
                    count += self._count_nodes(child, depth + 1)
            # Also check "data" field (jsMind format)
            if "data" in data and isinstance(data["data"], dict):
                count += self._count_nodes(data["data"], depth + 1)
        return count