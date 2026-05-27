"""
Video Slice Summary Skill

Identifies key teaching segments based on topic segmentation timestamps.
Marks segments with high interaction (peaks) and important content delivery moments.
"""

import json
from components.report_agent.skills.base_skill import BaseSkill


class VideoSliceSummarySkill(BaseSkill):
    name = "video_slice_summary"
    description = "Identify key video segments from topic segmentation timestamps. Marks interaction peaks and important teaching moments for video slicing."

    def execute(self, context: dict = None) -> dict:
        # Step 1: Get topic segmentation
        raw_topics = self.tools.execute_tool("get_topic_segmentation")

        if "NOT available" in raw_topics:
            return {
                "status": "unavailable",
                "result": None,
                "summary": "Topic segmentation data not available for video slicing.",
            }

        # Step 2: Parse topics
        try:
            topics_text = raw_topics.split("\n", 1)[1] if "\n" in raw_topics else raw_topics
            topics = json.loads(topics_text)
        except (json.JSONDecodeError, IndexError):
            return {
                "status": "partial",
                "result": {"error": "Failed to parse topic segmentation"},
                "summary": "Topic segmentation data could not be parsed.",
            }

        if not isinstance(topics, list) or len(topics) == 0:
            return {
                "status": "partial",
                "result": {"topics": []},
                "summary": "No topic segments found.",
            }

        # Step 3: Analyze segments — compute duration, identify key moments
        slices = []
        for i, topic in enumerate(topics):
            start = topic.get("start_time", 0)
            end = topic.get("end_time", 0)
            duration = end - start
            title = topic.get("topic", f"Segment {i+1}")

            slices.append({
                "segment_id": i + 1,
                "title": title,
                "start_time": round(start, 1),
                "end_time": round(end, 1),
                "duration_sec": round(duration, 1),
            })

        # Step 4: Identify key segments (longest = likely core content)
        sorted_by_duration = sorted(slices, key=lambda x: x["duration_sec"], reverse=True)
        key_segments = sorted_by_duration[:3]  # top 3 longest segments

        # Step 5: Use LLM to identify pedagogically important segments
        llm_highlights = ""
        if self.model and len(slices) > 0:
            segments_desc = "\n".join(
                [f"- [{s['start_time']}s - {s['end_time']}s] ({s['duration_sec']}s) {s['title']}" for s in slices[:15]]
            )
            llm_highlights = self._call_llm(
                f"""Given these class segments, identify the 3 most pedagogically important ones and explain why in 2-3 sentences each:

{segments_desc}

Format: numbered list with segment title and brief justification."""
            )

        result = {
            "total_segments": len(slices),
            "segments": slices,
            "key_segments": key_segments,
            "llm_highlights": llm_highlights,
        }

        return {
            "status": "success",
            "result": result,
            "summary": f"Identified {len(slices)} segments, top 3 key moments marked for video slicing.",
        }