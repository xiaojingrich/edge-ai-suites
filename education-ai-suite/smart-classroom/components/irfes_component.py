from __future__ import annotations

import json
import logging
import re
from typing import Any

from components.base_component import PipelineComponent
from utils.config_loader import config
from utils.markdown_cleaner import strip_think_tokens

logger = logging.getLogger(__name__)


class IRFESComponent(PipelineComponent):
    """POC component for three-step IRFES analysis using the configured LLM."""

    def __init__(self, session_id: str, temperature: float = 0.0):
        self.session_id = session_id
        self.temperature = temperature
        self.model = None

    @staticmethod
    def _extract_json_object(text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    @staticmethod
    def _clean_json_output(raw: str) -> dict[str, Any]:
        text = raw.strip()

        candidates = [text]

        # Remove fenced blocks if present.
        stripped = re.sub(r"```[a-zA-Z]*\n?([\s\S]*?)```", r"\1", text).strip()
        if stripped and stripped != text:
            candidates.append(stripped)

        extracted = IRFESComponent._extract_json_object(stripped)
        if extracted:
            candidates.append(extracted)

        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

        raise ValueError("INVALID_IRFES_FORMAT")

    @staticmethod
    def _parse_turns(transcript_text: str) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        last_idx = -1

        for raw_line in transcript_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Accept formats like: "TEACHER: ...", "教师: ...", "STUDENT_01: ...".
            match = re.match(r"^([^:]{1,30}):\s*(.+)$", line)
            if match:
                speaker = match.group(1).strip()
                text = match.group(2).strip()
                last_idx += 1
                turns.append({"turn_id": last_idx, "speaker": speaker, "text": text})
            elif turns:
                turns[-1]["text"] = f"{turns[-1]['text']} {line}".strip()

        return turns

    def _build_messages(self, turns: list[dict[str, Any]], language: str | None = None) -> list[dict[str, str]]:
        lang = (language or getattr(config.app, "language", "zh") or "zh").lower()
        use_zh = lang.startswith("zh")

        if use_zh:
            system_prompt = (
                "你是课堂互动分析引擎。必须严格执行三步判别法，并且只输出 JSON 对象。\n\n"
                "三步判别法：\n"
                "Step1 互动性筛选：若连续话轮中没有‘教师提问/指令 + 学生回应’，标记为 Non-Interactive。\n"
                "Step2 边界切片：识别每个独立互动事件的起点与终点。\n"
                "Step3 动态槽位匹配：对每个事件判断 I/R/F/E/S 是否激活，允许缺项。\n\n"
                "IRFES 槽位定义（严格按此判定）：\n"
                "- I：教师发问/发起任务（教师问）\n"
                "- R：学生针对 I 的应答（学生答）\n"
                "- F：教师对学生回答进行评价/判定正误（教师评）\n"
                "- E：教师追问、要求解释原因、拔高或拓展（老师追问/拓展）\n"
                "- S：师生共同总结规律，或引导迁移应用到新情境（共同总结/迁移）\n\n"
                "传统 IRF 判定补充：\n"
                "- 当一个事件块出现 I-R-F 且教师反馈已完成时，可视为基础闭环事件。\n"
                "- 若 F 之后继续出现追问/拓展，则进入 E；若出现归纳/迁移，则进入 S。\n\n"
                "输出要求：\n"
                "1. 输出一个 JSON 对象，不要 markdown，不要解释文字。\n"
                "2. turn_id 必须引用输入中的编号。\n"
                "3. confidence 取 0 到 1 的小数。\n"
                "4. 只有 I/R/F/E/S 全为 active=true，is_full_irfes 才能为 true。\n\n"
                "JSON 结构：\n"
                "{\n"
                "  \"meta\": {\"language\": \"zh\", \"total_turns\": 0},\n"
                "  \"events\": [\n"
                "    {\n"
                "      \"event_id\": \"E1\",\n"
                "      \"interaction_label\": \"Interactive\" | \"Non-Interactive\",\n"
                "      \"start_turn\": 0,\n"
                "      \"end_turn\": 0,\n"
                "      \"step1\": {\"is_interactive\": true, \"reason\": \"...\", \"evidence_turns\": [0]},\n"
                "      \"step2\": {\"start_reason\": \"...\", \"end_reason\": \"...\"},\n"
                "      \"step3\": {\n"
                "        \"I\": {\"active\": false, \"evidence_turns\": [], \"reason\": \"...\"},\n"
                "        \"R\": {\"active\": false, \"evidence_turns\": [], \"reason\": \"...\"},\n"
                "        \"F\": {\"active\": false, \"evidence_turns\": [], \"reason\": \"...\"},\n"
                "        \"E\": {\"active\": false, \"evidence_turns\": [], \"reason\": \"...\"},\n"
                "        \"S\": {\"active\": false, \"evidence_turns\": [], \"reason\": \"...\"}\n"
                "      },\n"
                "      \"is_full_irfes\": false,\n"
                "      \"confidence\": 0.0\n"
                "    }\n"
                "  ]\n"
                "}\n"
            )
            user_prompt = (
                "请根据下面的课堂话轮完成三步判别法分析。\n"
                "话轮数据(JSON数组)：\n"
                f"{json.dumps(turns, ensure_ascii=False)}"
            )
        else:
            system_prompt = (
                "You are a classroom interaction analysis engine. Follow a strict 3-step method and output JSON only.\n\n"
                "Step1 Interactivity filter: if no explicit 'teacher prompt/question + student response' pattern appears in consecutive turns, mark as Non-Interactive.\n"
                "Step2 Boundary slicing: detect start/end of each complete interaction event.\n"
                "Step3 Dynamic slot matching: detect activated slots among I/R/F/E/S for each event (no fixed order).\n\n"
                "IRFES slot definitions (strict):\n"
                "- I: Teacher initiation/question/task prompt (teacher asks)\n"
                "- R: Student response to I (student answers)\n"
                "- F: Teacher feedback/evaluation/correctness judgment (teacher evaluates)\n"
                "- E: Teacher follow-up probing, asking why/how, or extending the idea (teacher extends)\n"
                "- S: Joint summary or transfer/application to new contexts (joint summarize/transfer)\n\n"
                "Traditional IRF closure rule:\n"
                "- If an event contains I-R-F and teacher feedback has concluded, treat it as a complete base event.\n"
                "- If follow-up probing appears after F, continue with E; if summary/transfer appears, continue with S.\n\n"
                "Output rules:\n"
                "1. Output a single JSON object only (no markdown).\n"
                "2. turn_id references must come from input.\n"
                "3. confidence must be 0..1 float.\n"
                "4. is_full_irfes can be true only when I,R,F,E,S are all active=true.\n"
            )
            user_prompt = (
                "Analyze the classroom turns using the 3-step method and return JSON only.\n"
                "Turns (JSON array):\n"
                f"{json.dumps(turns, ensure_ascii=False)}"
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _heuristic_fallback(turns: list[dict[str, Any]], language: str | None = None) -> dict[str, Any]:
        """Fallback when model output is malformed. Produces one coarse event."""
        if not turns:
            return {
                "meta": {"language": language or "unknown", "total_turns": 0, "fallback": True},
                "events": [],
            }

        teacher_tokens = ("teacher", "教师")
        student_tokens = ("student", "学生")

        has_i = False
        has_r = False
        has_f = False
        has_e = False
        has_s = False
        i_turns: list[int] = []
        r_turns: list[int] = []
        f_turns: list[int] = []
        e_turns: list[int] = []
        s_turns: list[int] = []

        feedback_tokens = (
            "很好", "不错", "正确", "对", "不对", "答得", "yes", "good", "right", "correct", "exactly"
        )
        extend_tokens = (
            "为什么", "怎么", "还有", "进一步", "如果", "那么", "能不能", "再想想", "why", "how", "what else", "can you"
        )
        summary_tokens = (
            "总结", "归纳", "所以", "因此", "规律", "迁移", "应用", "举一反三", "in summary", "therefore", "apply", "transfer"
        )

        for t in turns:
            speaker = str(t.get("speaker", "")).lower()
            text = str(t.get("text", ""))
            text_l = text.lower()
            if any(tok in speaker for tok in teacher_tokens) and ("?" in text or "？" in text):
                has_i = True
                i_turns.append(int(t["turn_id"]))
            if any(tok in speaker for tok in student_tokens):
                has_r = True
                r_turns.append(int(t["turn_id"]))
            if any(tok in speaker for tok in teacher_tokens) and any(tok in text_l for tok in feedback_tokens):
                has_f = True
                f_turns.append(int(t["turn_id"]))
            if any(tok in speaker for tok in teacher_tokens) and any(tok in text_l for tok in extend_tokens):
                has_e = True
                e_turns.append(int(t["turn_id"]))
            if any(tok in text_l for tok in summary_tokens):
                has_s = True
                s_turns.append(int(t["turn_id"]))

        interactive = has_i and has_r
        full_irfes = has_i and has_r and has_f and has_e and has_s

        return {
            "meta": {"language": language or "unknown", "total_turns": len(turns), "fallback": True},
            "events": [
                {
                    "event_id": "E1",
                    "interaction_label": "Interactive" if interactive else "Non-Interactive",
                    "start_turn": 0,
                    "end_turn": len(turns) - 1,
                    "step1": {
                        "is_interactive": interactive,
                        "reason": "Fallback heuristic based on teacher question marks and student turns.",
                        "evidence_turns": sorted(set(i_turns + r_turns)),
                    },
                    "step2": {
                        "start_reason": "Fallback uses full transcript range.",
                        "end_reason": "Fallback uses full transcript range.",
                    },
                    "step3": {
                        "I": {"active": has_i, "evidence_turns": i_turns, "reason": "Teacher question punctuation."},
                        "R": {"active": has_r, "evidence_turns": r_turns, "reason": "Student response turns present."},
                        "F": {"active": has_f, "evidence_turns": f_turns, "reason": "Teacher evaluative keywords."},
                        "E": {"active": has_e, "evidence_turns": e_turns, "reason": "Teacher follow-up/extension keywords."},
                        "S": {"active": has_s, "evidence_turns": s_turns, "reason": "Summary/transfer keywords."},
                    },
                    "is_full_irfes": full_irfes,
                    "confidence": 0.45 if full_irfes else 0.35,
                }
            ],
        }

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _clamp(value: int, min_v: int, max_v: int) -> int:
        return max(min_v, min(max_v, value))

    @staticmethod
    def _normalize_evidence_turns(raw: Any, max_turn: int) -> list[int]:
        if not isinstance(raw, list):
            return []
        normalized: list[int] = []
        seen: set[int] = set()
        for item in raw:
            try:
                idx = int(item)
            except Exception:
                continue
            if idx < 0 or idx > max_turn or idx in seen:
                continue
            normalized.append(idx)
            seen.add(idx)
        return normalized

    @staticmethod
    def _normalize_slot(slot_raw: Any, max_turn: int) -> dict[str, Any]:
        slot = slot_raw if isinstance(slot_raw, dict) else {}
        evidence_turns = IRFESComponent._normalize_evidence_turns(slot.get("evidence_turns", []), max_turn)
        active = bool(slot.get("active", False)) and len(evidence_turns) > 0
        reason = str(slot.get("reason", "")).strip()
        if not reason:
            reason = "Calibrated from model output and evidence consistency."
        return {
            "active": active,
            "evidence_turns": evidence_turns,
            "reason": reason,
        }

    @staticmethod
    def _calibrate_output(parsed: dict[str, Any], turns: list[dict[str, Any]], language: str | None = None) -> dict[str, Any]:
        total_turns = len(turns)
        max_turn = total_turns - 1

        meta = parsed.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["language"] = language or meta.get("language", "unknown")
        meta["total_turns"] = total_turns
        meta["calibrated"] = True

        raw_events = parsed.get("events", [])
        if not isinstance(raw_events, list):
            raw_events = []

        calibrated_events: list[dict[str, Any]] = []
        for i, event_raw in enumerate(raw_events, start=1):
            event = event_raw if isinstance(event_raw, dict) else {}

            if total_turns > 0:
                start_turn = IRFESComponent._to_int(event.get("start_turn", 0), 0)
                end_turn = IRFESComponent._to_int(event.get("end_turn", max_turn), max_turn)
                start_turn = IRFESComponent._clamp(start_turn, 0, max_turn)
                end_turn = IRFESComponent._clamp(end_turn, 0, max_turn)
                if end_turn < start_turn:
                    end_turn = start_turn
            else:
                start_turn = 0
                end_turn = 0

            step1 = event.get("step1", {}) if isinstance(event.get("step1", {}), dict) else {}
            step2 = event.get("step2", {}) if isinstance(event.get("step2", {}), dict) else {}
            step3_raw = event.get("step3", {}) if isinstance(event.get("step3", {}), dict) else {}

            slots = {
                "I": IRFESComponent._normalize_slot(step3_raw.get("I"), max_turn),
                "R": IRFESComponent._normalize_slot(step3_raw.get("R"), max_turn),
                "F": IRFESComponent._normalize_slot(step3_raw.get("F"), max_turn),
                "E": IRFESComponent._normalize_slot(step3_raw.get("E"), max_turn),
                "S": IRFESComponent._normalize_slot(step3_raw.get("S"), max_turn),
            }

            is_interactive = slots["I"]["active"] and slots["R"]["active"]
            is_full_irfes = all(slots[k]["active"] for k in ["I", "R", "F", "E", "S"])

            step1_evidence = IRFESComponent._normalize_evidence_turns(step1.get("evidence_turns", []), max_turn)
            if not step1_evidence:
                step1_evidence = sorted(set(slots["I"]["evidence_turns"] + slots["R"]["evidence_turns"]))

            step1_reason = str(step1.get("reason", "")).strip() or "Calibrated from I/R evidence."
            step2_start_reason = str(step2.get("start_reason", "")).strip() or "Calibrated start boundary."
            step2_end_reason = str(step2.get("end_reason", "")).strip() or "Calibrated end boundary."

            active_count = sum(1 for k in ["I", "R", "F", "E", "S"] if slots[k]["active"])
            confidence = round(min(0.95, 0.2 + 0.12 * active_count + (0.05 if is_interactive else 0.0)), 2)

            calibrated_events.append(
                {
                    "event_id": str(event.get("event_id", f"E{i}")),
                    "interaction_label": "Interactive" if is_interactive else "Non-Interactive",
                    "start_turn": start_turn,
                    "end_turn": end_turn,
                    "step1": {
                        "is_interactive": is_interactive,
                        "reason": step1_reason,
                        "evidence_turns": step1_evidence,
                    },
                    "step2": {
                        "start_reason": step2_start_reason,
                        "end_reason": step2_end_reason,
                    },
                    "step3": slots,
                    "is_full_irfes": is_full_irfes,
                    "confidence": confidence,
                }
            )

        # Keep event boundaries monotonic and avoid overlap for deterministic output.
        calibrated_events.sort(key=lambda e: (e["start_turn"], e["end_turn"]))
        prev_end = -1
        for event in calibrated_events:
            if event["start_turn"] <= prev_end:
                event["start_turn"] = prev_end + 1
                if event["end_turn"] < event["start_turn"]:
                    event["end_turn"] = event["start_turn"]
            prev_end = event["end_turn"]

        return {
            "meta": meta,
            "events": calibrated_events,
        }

    def generate_irfes(self, transcript_text: str, language: str | None = None) -> dict[str, Any]:
        if self.model is None:
            raise ValueError("IRFES model is not attached. Set component.model before calling generate_irfes().")

        turns = self._parse_turns(transcript_text)
        if not turns:
            return {
                "meta": {"language": language or getattr(config.app, "language", "unknown"), "total_turns": 0},
                "events": [],
            }

        prompt = self.model.tokenizer.apply_chat_template(
            self._build_messages(turns, language=language),
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )

        try:
            raw = strip_think_tokens(self.model.generate(prompt, False))
            parsed = self._clean_json_output(raw)
            if "meta" not in parsed or not isinstance(parsed["meta"], dict):
                parsed["meta"] = {}
            parsed["meta"]["source"] = "model"
            calibrated = self._calibrate_output(parsed, turns, language=language)
            return calibrated
        except Exception as exc:
            logger.warning("IRFES model parsing failed, fallback to heuristic mode: %s", exc)
            fallback = self._heuristic_fallback(turns, language=language)
            if "meta" not in fallback or not isinstance(fallback["meta"], dict):
                fallback["meta"] = {}
            fallback["meta"]["source"] = "fallback"
            fallback["meta"]["fallback_error"] = str(exc)
            calibrated = self._calibrate_output(fallback, turns, language=language)
            return calibrated
