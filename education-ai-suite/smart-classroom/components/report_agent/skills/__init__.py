from components.report_agent.skills.base_skill import BaseSkill
from components.report_agent.skills.engagement_analysis import EngagementAnalysisSkill
from components.report_agent.skills.video_slice_summary import VideoSliceSummarySkill
from components.report_agent.skills.content_analysis import ContentAnalysisSkill
from components.report_agent.skills.ocr_board_analysis import OCRBoardAnalysisSkill
from components.report_agent.skills.quiz_generation import QuizGenerationSkill
from components.report_agent.skills.teacher_behavior import TeacherBehaviorSkill

SKILL_REGISTRY = {
    "engagement_analysis": EngagementAnalysisSkill,
    "video_slice_summary": VideoSliceSummarySkill,
    "content_analysis": ContentAnalysisSkill,
    "ocr_board_analysis": OCRBoardAnalysisSkill,
    "quiz_generation": QuizGenerationSkill,
    "teacher_behavior": TeacherBehaviorSkill,
}