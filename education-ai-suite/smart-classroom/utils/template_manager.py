"""
Report Template Manager.

Handles loading .docx templates, extracting their structure (sections + placeholders),
and filling templates with LLM-generated content.

Template format:
- Headings define sections
- Text inside {placeholder} marks fields the LLM should fill
- Static text (without placeholders) is preserved as-is
"""

import os
import re
import json
import logging
from copy import deepcopy
from pathlib import Path
from docx import Document
from docx.shared import Pt

from utils.runtime_config_loader import RuntimeConfig

logger = logging.getLogger(__name__)

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


def get_template_path(language: str = "zh", session_id: str = None) -> str:
    """Get the active template path. Checks for custom template first, then default."""
    if session_id:
        project_config = RuntimeConfig.get_section("Project")
        custom_path = os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            session_id,
            "custom_report_template.docx",
        )
        if os.path.exists(custom_path):
            return custom_path

    project_config = RuntimeConfig.get_section("Project")
    project_custom = os.path.join(
        project_config.get("location"),
        project_config.get("name"),
        "report_template.docx",
    )
    if os.path.exists(project_custom):
        return project_custom

    default_path = os.path.join(TEMPLATES_DIR, f"report_template_{language}.docx")
    if os.path.exists(default_path):
        return default_path

    return None


def extract_template_structure(template_path: str) -> dict:
    """Extract the structure from a .docx template.

    Returns a dict with:
      - sections: list of {heading, level, fields: [field_names]}
      - all_fields: flat list of all placeholder names
      - raw_text: full template text for LLM reference
    """
    doc = Document(template_path)
    sections = []
    all_fields = []
    raw_lines = []
    current_section = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        raw_lines.append(text)

        if para.style.name.startswith('Heading'):
            level = int(para.style.name.replace('Heading ', '').replace('Heading', '1'))
            current_section = {"heading": text, "level": level, "fields": []}
            sections.append(current_section)
        else:
            placeholders = re.findall(r'\{(\w+)\}', text)
            if placeholders:
                all_fields.extend(placeholders)
                if current_section:
                    current_section["fields"].extend(placeholders)

    return {
        "sections": sections,
        "all_fields": list(dict.fromkeys(all_fields)),
        "raw_text": "\n".join(raw_lines),
    }


def build_template_fill_prompt(template_structure: dict, collected_observations: str, language: str = "zh",
                               structured_stats: str = None) -> str:
    """Build a prompt that asks the LLM to fill template fields based on collected data.

    Args:
        structured_stats: Pre-computed statistics as readable text, shown prominently
                         so the LLM can directly copy values instead of computing them.

    Returns the user prompt text (not tokenized).
    """
    fields = template_structure["all_fields"]
    raw_text = template_structure["raw_text"]

    stats_section = ""
    if structured_stats:
        if language == "zh":
            stats_section = f"\n## 已计算的精确数据（直接使用这些数值，不要自己计算）：\n{structured_stats}\n"
        else:
            stats_section = f"\n## Pre-computed exact data (use these values directly, do NOT compute yourself):\n{structured_stats}\n"

    if language == "zh":
        prompt = f"""你是一个课堂评估报告生成器。根据收集到的课堂数据，按照报告模板的结构填写所有字段。
{stats_section}
## 报告模板结构：
{raw_text}

## 收集到的课堂原始数据：
{collected_observations}

## 任务：
请根据以上数据，为模板中的每个占位字段生成对应内容。输出严格的JSON格式，key为字段名，value为填充内容。

需要填写的字段：
{json.dumps(fields, ensure_ascii=False)}

## 重要规则：
- 数值类字段（时长、人数、语速、举手次数等）直接使用"已计算的精确数据"中的值，不要自己推算
- 定性分析字段（评估、建议、趋势等）根据原始数据进行分析
- 如果某个字段的数据确实不可用，填写"暂无数据"
- recommendations: 2-3条具体改进建议，用换行符分隔
- keywords: 用顿号（、）分隔关键词
- mindmap_summary: 描述知识结构的层次关系
- 输出纯JSON，不要包含```json标记或其他文字

输出JSON："""
    else:
        prompt = f"""You are a classroom evaluation report generator. Based on the collected classroom data, fill in all template fields.
{stats_section}
## Report Template Structure:
{raw_text}

## Collected Classroom Raw Data:
{collected_observations}

## Task:
Based on the data above, generate content for each placeholder field in the template. Output strict JSON format, with field names as keys and fill content as values.

Fields to fill:
{json.dumps(fields, ensure_ascii=False)}

## Important Rules:
- Numeric fields (duration, attendance, speaking speed, hand raises, etc.): use values from "Pre-computed exact data" directly, do NOT compute yourself
- Qualitative fields (assessments, recommendations, trends): analyze based on raw data
- If data for a field is genuinely unavailable, fill with "Data not available"
- recommendations: 2-3 specific suggestions, separated by newlines
- keywords: separate with commas
- mindmap_summary: describe the knowledge structure hierarchy
- Output pure JSON only, no ```json markers or other text

Output JSON:"""

    return prompt


def fill_template(template_path: str, field_values: dict, output_path: str) -> str:
    """Fill a .docx template with values and save to output_path.

    Replaces {placeholder} patterns in all paragraphs and tables with the provided values.
    """
    doc = Document(template_path)

    for para in doc.paragraphs:
        _replace_placeholders_in_paragraph(para, field_values)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_placeholders_in_paragraph(para, field_values)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    logger.info(f"Template-based report saved to {output_path}")
    return output_path


def _replace_placeholders_in_paragraph(paragraph, field_values: dict):
    """Replace {field_name} placeholders in a paragraph while preserving formatting."""
    full_text = paragraph.text
    if '{' not in full_text:
        return

    placeholders = re.findall(r'\{(\w+)\}', full_text)
    if not placeholders:
        return

    new_text = full_text
    for field_name in placeholders:
        value = field_values.get(field_name, "")
        new_text = new_text.replace(f'{{{field_name}}}', str(value))

    if new_text == full_text:
        return

    for i, run in enumerate(paragraph.runs):
        if i == 0:
            run.text = new_text
        else:
            run.text = ""


def parse_llm_json_response(response_text: str) -> dict:
    """Parse LLM response as JSON, handling common formatting issues."""
    text = response_text.strip()

    if text.startswith("[ERROR]:"):
        raise ValueError(f"LLM returned an error instead of JSON: {text}")

    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

    logger.warning(f"Failed to parse LLM JSON response: {text[:200]}")
    return {}
