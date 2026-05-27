"""
Teacher Behavior Analysis Skill

Analyzes teacher behavioral patterns:
- Movement range (teacher in students' area vs podium)
- Board writing frequency
- Lecture vs interaction time ratio
- Teaching style assessment
"""

import os
import json
from components.report_agent.skills.base_skill import BaseSkill


class TeacherBehaviorSkill(BaseSkill):
    name = "teacher_behavior"
    description = "Analyze teacher behavior patterns: movement range, lecture vs interaction ratio, board usage frequency, and teaching style assessment."

    def execute(self, context: dict = None) -> dict:
        from components.report_agent.tools import get_session_dir

        session_dir = get_session_dir(self.session_id)

        # Step 1: Check for back camera data (shows teacher movement)
        back_posture_path = os.path.join(session_dir, "va", "back_posture.txt")
        back_resnet_path = os.path.join(session_dir, "va", "back_resnet18.txt")

        has_back_data = os.path.exists(back_posture_path)
        has_classification = os.path.exists(back_resnet_path)

        # Step 2: Get transcription for lecture/interaction ratio
        summary_raw = self.tools.execute_tool("get_class_summary")
        has_summary = "NOT available" not in summary_raw and "empty" not in summary_raw

        if not has_back_data and not has_summary:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Neither video analytics (back camera) nor transcription available for teacher behavior analysis.",
            }

        # Step 3: Analyze back camera data for movement patterns
        movement_data = {}
        if has_back_data:
            try:
                with open(back_posture_path, "r") as f:
                    lines = f.readlines()

                total_frames = len(lines)
                teacher_detected_frames = 0

                for line in lines:
                    line = line.strip()
                    if line:
                        try:
                            frame = json.loads(line)
                            persons = frame.get("persons", frame.get("detections", []))
                            if persons:
                                teacher_detected_frames += 1
                        except json.JSONDecodeError:
                            pass

                movement_data = {
                    "total_frames_analyzed": total_frames,
                    "teacher_visible_frames": teacher_detected_frames,
                    "teacher_visibility_ratio": round(teacher_detected_frames / max(total_frames, 1), 2),
                }
            except Exception as e:
                movement_data = {"error": str(e)}

        # Step 4: Analyze classification results for activity types
        activity_data = {}
        if has_classification:
            try:
                with open(back_resnet_path, "r") as f:
                    lines = f.readlines()

                activity_counts = {}
                for line in lines:
                    line = line.strip()
                    if line:
                        try:
                            frame = json.loads(line)
                            label = frame.get("label", frame.get("classification", "unknown"))
                            activity_counts[label] = activity_counts.get(label, 0) + 1
                        except json.JSONDecodeError:
                            pass

                activity_data = {
                    "activity_distribution": activity_counts,
                    "total_classified_frames": sum(activity_counts.values()),
                }
            except Exception as e:
                activity_data = {"error": str(e)}

        # Step 5: Estimate lecture vs interaction ratio from summary
        lecture_interaction_analysis = ""
        if self.model and has_summary:
            summary_text = "\n".join(summary_raw.split("\n")[1:])
            lecture_interaction_analysis = self._call_llm(
                f"""Based on this class summary, estimate the teacher's teaching style:

{summary_text[:2000]}

Analyze:
1. Approximate lecture vs interaction time ratio (e.g., 70:30)
2. Teaching style (Lecture-dominant / Interactive / Mixed / Discussion-based)
3. Frequency of Q&A moments
4. One-line assessment of teacher engagement with students

Be concise, use bullet points."""
            )

        result = {
            "has_video_data": has_back_data,
            "has_classification_data": has_classification,
            "movement_analysis": movement_data,
            "activity_analysis": activity_data,
            "lecture_interaction_analysis": lecture_interaction_analysis,
        }

        status = "success" if has_back_data else "partial"

        return {
            "status": status,
            "result": result,
            "summary": f"Teacher behavior: video data {'available' if has_back_data else 'unavailable'}, "
                       f"classification {'available' if has_classification else 'unavailable'}.",
        }