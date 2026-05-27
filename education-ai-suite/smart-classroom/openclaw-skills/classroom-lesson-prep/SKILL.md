---
name: classroom-lesson-prep
description: "Assist with lesson planning, teaching design, and course preparation."
metadata:
  openclaw:
    emoji: "📚"
---

# Classroom Lesson Prep Agent (备课Agent)

Assists teachers with lesson planning, teaching design, and course preparation.

## When to Use (Trigger Phrases)

Use this skill when the user asks any of:

- "帮我备课" / "Help me prepare a lesson"
- "教学设计" / "Teaching design"
- "课程规划" / "Course planning"
- "生成教案" / "Generate lesson plan"
- "教学目标" / "Teaching objectives"
- Any question about lesson planning, curriculum design, or teaching preparation

## How to Call

```bash
curl -X POST http://localhost:8000/agent/route \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<SESSION_ID>", "message": "<USER_QUESTION>"}'
```

## Status

🚧 **Under Development** — This agent is planned for a future release.

Currently, the system will acknowledge the request and inform the user that lesson prep features are coming soon.

## Planned Capabilities

- Generate lesson plans based on curriculum standards
- Suggest teaching activities for specific topics
- Create differentiated instruction materials
- Recommend teaching resources
- Design assessment rubrics
- Align objectives with learning outcomes
- Leverage previous class data to inform planning (via report agent's memory)