from components.base_component import PipelineComponent
from utils.runtime_config_loader import RuntimeConfig
from utils.config_loader import config
from utils.storage_manager import StorageManager
import json
import logging, os

logger = logging.getLogger(__name__)

class MindmapComponent(PipelineComponent):
    def __init__(self, session_id, provider, model_name, device, temperature=0.7):
        self.session_id = session_id
        self.provider = provider.lower()
        self.model_name = model_name
        self.device = device
        self.temperature = temperature

    def _get_mindmap_message(self, input_text):
        lang_prompt = vars(config.mindmap.system_prompt)
        logger.debug(f"Mindmap System Prompt: {lang_prompt.get(config.app.language)}")
        return [
            {"role": "system", "content": f"{lang_prompt.get(config.app.language)}"},
            {"role": "user", "content": f"{input_text}"}
        ]

    def generate_mindmap(self, summary_text):
        project_config = RuntimeConfig.get_section("Project")
        project_path = os.path.join(
            project_config.get("location"),
            project_config.get("name"),
            self.session_id
        )
        mindmap_path = os.path.join(project_path, "mindmap.mmd")

        try:
            logger.info("Generating mindmap from summary...")
            full_mindmap = self._try_generate(summary_text)
            full_mindmap = self._normalize_mindmap_json(full_mindmap)
            StorageManager.save(mindmap_path, full_mindmap, append=False)
            logger.info("Mindmap generation completed successfully.")
            return full_mindmap

        except Exception as e:
            logger.error(f"Mindmap generation failed: {e}")
            raise e

    def _normalize_mindmap_json(self, raw_text: str) -> str:
        """Normalize common LLM formatting mistakes in jsMind JSON output."""
        if not isinstance(raw_text, str):
            return raw_text

        text = raw_text.strip()
        if text.startswith("[ERROR]:"):
            return raw_text

        try:
            data = json.loads(text)
        except Exception:
            return raw_text

        if isinstance(data, dict) and not data.get("format") and data.get("!format"):
            data["format"] = data.pop("!format")

        return json.dumps(data, ensure_ascii=False, indent=2)

    def _try_generate(self, text):
        """Attempt generation with truncation retry on probability tensor errors."""
        max_input_chars = len(text)
        for attempt in range(2):
            try:
                input_text = text[:max_input_chars]
                prompt = self.model.tokenizer.apply_chat_template(
                    self._get_mindmap_message(input_text),
                    tokenize=False,
                    add_generation_prompt=True
                )
                result = self.model.generate(prompt, False)
                if isinstance(result, str) and result.startswith("[ERROR]:"):
                    raise RuntimeError(result)
                return result
            except Exception as e:
                if "probability tensor" in str(e).lower() and attempt == 0:
                    max_input_chars = int(max_input_chars * 0.6)
                    logger.warning(f"Probability tensor error, retrying with truncated input ({max_input_chars} chars)...")
                    continue
                raise