"""
Prompts for the Report Generator.

Contains:
- Report generation prompts (free-form markdown output)
- Template fill prompts (structured JSON output for .docx template filling)
"""

REPORT_GENERATION_PROMPT_EN = """Based on the collected classroom data below, generate a comprehensive Classroom Evaluation Report.

## Collected Data:
{collected_data}

## Output Format — Generate the report using EXACTLY these sections:

# Classroom Evaluation Report

## 1. Statistical Overview
| Metric | Value |
|--------|-------|
(Fill from collected statistics, or mark "N/A" if not available)

## 2. Student Engagement Analysis
- Engagement level assessment (High/Medium/Low) with justification
- Notable patterns from the data

## 3. Class Content Summary
- Main teaching objectives covered
- Key points from the lesson
- Student questions (if observed)

## 4. Knowledge Structure
- Core topics and their relationships (from mindmap/segmentation)

## 5. Teaching Effectiveness Indicators
- Content delivery assessment
- Student participation level
- Interactive moments

## 6. Recommendations
- 2-3 actionable suggestions for the teacher

## Rules:
- Use ONLY the collected data. Do NOT invent statistics.
- Mark unavailable sections as "Data not available for this session"
- Keep the report between 400-800 words
- Be professional and objective
"""

REPORT_GENERATION_PROMPT_ZH = """根据以下收集到的课堂数据，生成一份全面的课堂评估报告。

## 收集到的数据：
{collected_data}

## 输出格式 — 请严格按照以下章节生成报告：

# 课堂评估报告

## 1. 统计概览
| 指标 | 数值 |
|------|------|
（从收集的统计数据中填写，不可用则标注"暂无数据"）

## 2. 学生参与度分析
- 参与度水平评估（高/中/低）及理由
- 数据中的显著模式

## 3. 课堂内容摘要
- 本节课覆盖的主要教学目标
- 课程要点
- 学生提问（如有观察到）

## 4. 知识结构
- 核心主题及其关系（来自思维导图/内容分割）

## 5. 教学效果指标
- 内容传达评估
- 学生参与水平
- 互动时刻

## 6. 建议
- 为教师提供2-3条可操作的建议

## 规则：
- 仅使用收集到的数据，不要编造统计数据
- 不可用的部分标注"本次课程数据暂不可用"
- 报告保持在400-800字之间
- 保持专业客观
"""

# --- Template Fill Prompts ---
# Used when a .docx template with {placeholder} fields is available.
# The LLM generates a JSON object mapping field names to content values.

TEMPLATE_FILL_SYSTEM_EN = "You are a professional educational analyst. Fill report template fields based on provided data. Output JSON."

TEMPLATE_FILL_SYSTEM_ZH = "你是一个专业的教育分析师。根据提供的数据填充报告模板字段，输出JSON。"

TEMPLATE_FILL_PROMPT_EN = """You are a classroom evaluation report generator. Based on the collected classroom data, fill in all template fields.

## Report Template Structure:
{template_raw_text}

## Collected Classroom Data:
{collected_data}

## Task:
Based on the data above, generate content for each placeholder field in the template. Output strict JSON format, with field names as keys and fill content as values.

Fields to fill:
{fields_json}

## Rules:
- Use ONLY the collected data, do NOT invent statistics
- If data for a field is unavailable, fill with "Data not available"
- Numeric fields: use numbers or values with units
- Descriptive fields: use concise sentences, no more than 2-3 sentences
- recommendations field: separate multiple items with newlines
- keywords field: separate with commas
- Output pure JSON only, no ```json markers or other text

Output JSON:"""

TEMPLATE_FILL_PROMPT_ZH = """你是一个课堂评估报告生成器。根据收集到的课堂数据，按照报告模板的结构填写所有字段。

## 报告模板结构：
{template_raw_text}

## 收集到的课堂数据：
{collected_data}

## 任务：
请根据以上数据，为模板中的每个占位字段生成对应内容。输出严格的JSON格式，key为字段名，value为填充内容。

需要填写的字段：
{fields_json}

## 规则：
- 仅使用收集到的数据，不要编造统计数据
- 如果某个字段的数据不可用，填写"暂无数据"
- 数值型字段直接填数字或带单位的值
- 描述型字段用简洁的句子，不超过2-3句话
- recommendations 字段用换行符分隔多条建议
- keywords 字段用顿号（、）分隔关键词
- 输出纯JSON，不要包含```json标记或其他文字

输出JSON："""
