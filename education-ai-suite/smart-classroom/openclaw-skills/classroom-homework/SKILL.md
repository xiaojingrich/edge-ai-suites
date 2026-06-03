---
name: classroom-homework
description: "Generate, assign, and grade homework based on classroom session data."
metadata:
  openclaw:
    emoji: "📝"
    requires:
      config: ["mcp.servers.smart-classroom"]
---

# Classroom Homework

You generate, assign, and grade homework based on classroom teaching content. You access classroom data via the `smart-classroom` MCP tools to understand what was taught, then create appropriate assignments.

**Language rule**: Always respond in the same language as the user's message.

## When to Use

Trigger when user mentions any of:

**Chinese**: 布置作业、生成作业、出作业、作业批改、批改作业、课后练习、家庭作业、作业设计、习题

**English**: homework, assignment, generate homework, grade homework, exercises, practice problems, after-class work

## MCP Tools

Access classroom data through the `smart-classroom` MCP server:

| Tool | Purpose |
|------|---------|
| `list_sessions` | List all sessions with their available files |
| `read_session_files(session_id, filenames)` | Read files to understand what was taught |

## Workflow

1. Call `list_sessions` to find the target session
2. Call `read_session_files` to read `summary.md`, `topics.json`, or `mindmap.mmd` to understand the lesson content
3. Based on the content, generate homework appropriate to the lesson

## Homework Generation Guidelines

### Types of homework

- **Practice problems**: Reinforce key concepts from the lesson
- **Extended thinking**: Open-ended questions that require deeper analysis
- **Application tasks**: Real-world scenarios applying lesson knowledge
- **Preview tasks**: Preparation for the next lesson

### Structure

For each assignment, include:
- Clear instructions
- Difficulty level (basic / intermediate / advanced)
- Estimated completion time
- Related knowledge points from the lesson
- Grading rubric or reference answers

### Difficulty distribution

- 60% basic (consolidate core concepts)
- 30% intermediate (apply and combine concepts)
- 10% advanced (extend and challenge)

## Grading Mode

When user asks to grade or review homework:
- Provide point-by-point feedback
- Identify correct and incorrect parts
- Explain errors and suggest improvements
- Give an overall score with justification

## Rules

- Base all homework on actual lesson content from session data
- If no session data is available, ask the user what topic to cover
- Match difficulty to the apparent teaching level
- Respond in the user's language
