---
name: classroom-homework
description: "Grade homework, analyze assignment results, and provide feedback on student submissions."
metadata:
  openclaw:
    emoji: "📝"
---

# Classroom Homework Agent (作业Agent)

Handles homework grading, assignment analysis, and student feedback.

## When to Use (Trigger Phrases)

Use this skill when the user asks any of:

- "批改作业" / "Grade homework"
- "作业分析" / "Analyze assignments"
- "作业反馈" / "Homework feedback"
- "成绩统计" / "Grade statistics"
- "错误率分析" / "Error rate analysis"
- Any question about homework, assignments, grading, or scores

## How to Call

```bash
curl -X POST http://localhost:8000/agent/route \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<SESSION_ID>", "message": "<USER_QUESTION>"}'
```

## Status

🚧 **Under Development** — This agent is planned for a future release.

Currently, the system will acknowledge the request and inform the user that homework analysis features are coming soon.

## Planned Capabilities

- Batch homework grading (OCR + LLM analysis)
- Error pattern detection across class
- Individual student feedback generation
- Grade distribution statistics
- Common mistake analysis
- Personalized improvement suggestions