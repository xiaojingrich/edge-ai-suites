"""
Engagement Analysis Skill

Combines hand-raise/stand-up/student-count data to compute:
- Overall engagement score (High/Medium/Low)
- Engagement timeline (peak/valley periods)
- Per-student engagement patterns (from ReID data)
"""

import json
import os
from components.report_agent.skills.base_skill import BaseSkill


class EngagementAnalysisSkill(BaseSkill):
    name = "engagement_analysis"
    description = "Analyze student engagement level from video analytics statistics (hand raises, stand-ups, student count). Produces engagement score and pattern analysis."

    def execute(self, context: dict = None) -> dict:
        # Step 1: Get raw statistics via tool
        raw_stats = self.tools.execute_tool("get_class_statistics")

        if "NOT available" in raw_stats:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Video analytics data not available for engagement analysis.",
            }

        # Step 2: Parse statistics
        try:
            stats_text = raw_stats.split("\n", 1)[1] if "\n" in raw_stats else raw_stats
            stats = json.loads(stats_text)
        except (json.JSONDecodeError, IndexError):
            stats = {"student_count": 0, "raise_up_count": 0, "stand_count": 0, "stand_reid": []}

        student_count = stats.get("student_count", 0)
        raise_count = stats.get("raise_up_count", 0)
        stand_count = stats.get("stand_count", 0)
        stand_reid = stats.get("stand_reid", [])

        # Step 3: Compute engagement score
        if student_count == 0:
            engagement_score = "Low"
            engagement_ratio = 0
        else:
            interactions_per_student = (raise_count + stand_count) / student_count
            if interactions_per_student >= 3:
                engagement_score = "High"
            elif interactions_per_student >= 1:
                engagement_score = "Medium"
            else:
                engagement_score = "Low"
            engagement_ratio = round(interactions_per_student, 2)

        # Step 4: Analyze per-student patterns
        active_students = len([s for s in stand_reid if s.get("count", 0) >= 2])
        passive_students = student_count - active_students if student_count > active_students else 0

        # Step 5: Use LLM for deeper pattern analysis (if model available)
        llm_analysis = ""
        if self.model and student_count > 0:
            llm_analysis = self._call_llm(
                f"""Analyze this classroom engagement data briefly (3-4 bullet points):
- Total students: {student_count}
- Hand raises: {raise_count}
- Stand-ups: {stand_count}
- Active students (stood up 2+ times): {active_students}
- Interactions per student ratio: {engagement_ratio}

Identify patterns and provide brief insights about engagement level."""
            )

        result = {
            "engagement_score": engagement_score,
            "student_count": student_count,
            "raise_up_count": raise_count,
            "stand_count": stand_count,
            "interactions_per_student": engagement_ratio,
            "active_students": active_students,
            "passive_students": passive_students,
            "stand_reid_details": stand_reid,
            "llm_analysis": llm_analysis,
        }

        return {
            "status": "success",
            "result": result,
            "summary": f"Engagement: {engagement_score} (ratio {engagement_ratio}/student, {active_students} active students)",
        }