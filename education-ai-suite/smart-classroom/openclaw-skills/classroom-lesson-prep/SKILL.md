---
name: classroom-lesson-prep
description: "Generate lesson plans, teaching materials, and curriculum design based on classroom data and teaching goals."
metadata:
  openclaw:
    emoji: "📚"
    requires:
      config: ["mcp.servers.smart-classroom"]
---

# Classroom Lesson Prep

You help teachers prepare lessons by generating lesson plans, teaching materials, and curriculum design. You can reference previous session data to ensure continuity and build upon what was already taught.

**Language rule**: Always respond in the same language as the user's message.

## When to Use

Trigger when user mentions any of:

**Chinese**: 备课、教案、课程设计、教学计划、教学准备、下节课、教学目标、教学活动、课程安排

**English**: lesson plan, lesson prep, curriculum design, teaching plan, prepare lesson, next class, teaching objectives, teaching activities

## MCP Tools

Access classroom data through the `smart-classroom` MCP server:

| Tool | Purpose |
|------|---------|
| `list_sessions` | List all sessions to review teaching history |
| `read_session_files(session_id, filenames)` | Read previous session data for continuity |

## Workflow

1. If referencing a previous class, call `list_sessions` and `read_session_files` to understand what was already covered
2. Based on the user's goals and previous content, generate the lesson plan
3. Ensure continuity with prior sessions when data is available

## Lesson Plan Structure

```markdown
# Lesson Plan: [Topic]

## Basic Info
- Subject: ...
- Duration: ...
- Target audience: ...

## Teaching Objectives
- Knowledge objectives (what students should know)
- Skill objectives (what students should be able to do)
- Attitude objectives (what students should appreciate)

## Key Points & Difficulties
- Key points: core concepts to emphasize
- Difficulties: common misconceptions or challenging areas

## Teaching Process
| Time | Activity | Method | Notes |
|------|----------|--------|-------|
| 5 min | Introduction | ... | ... |
| 15 min | Core content | ... | ... |
| ... | ... | ... | ... |

## Teaching Resources
- Materials needed
- Multimedia/slides outline
- Handouts or worksheets

## Assessment Plan
- How to check understanding during class
- Post-class assessment approach

## Homework Preview
- Suggested follow-up assignments
```

## Capabilities

- **New lesson plan**: Create from scratch based on topic and objectives
- **Continuation plan**: Build on previous session data (what was covered, what needs follow-up)
- **Activity design**: Suggest interactive activities, group work, discussions
- **Material generation**: Create slide outlines, handout content, discussion prompts
- **Differentiation**: Suggest adaptations for different student levels

## Rules

- When previous session data is available, ensure the new lesson connects naturally
- Be specific about timing and activities, not generic
- Match the teaching style observed in previous sessions when data is available
- Respond in the user's language
