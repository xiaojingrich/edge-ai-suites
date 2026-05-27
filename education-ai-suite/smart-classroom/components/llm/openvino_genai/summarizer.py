from components.llm.base_summarizer import BaseSummarizer
import openvino_genai as ov_genai
from transformers import AutoTokenizer
import logging, threading, gc
from utils import ensure_model
from utils.config_loader import config
from utils.ov_genai_util import YieldingTextStreamer
from utils.locks import audio_pipeline_lock
logger = logging.getLogger(__name__)

class Summarizer(BaseSummarizer):
    def __init__(self, model_name, device, temperature=0.7, revision=None):
        self.model_name = model_name
        self.device = device
        self.temperature = temperature
        self._held_model = None
        logger.info(f"Loading Model: model name={self.model_name}, model path={ensure_model.get_model_path()}, device={self.device}")
        self.tokenizer = AutoTokenizer.from_pretrained(ensure_model.get_model_path())

    def acquire_model(self):
        if self._held_model is None:
            self._held_model = self._load_model()
            logger.info("Model acquired and held in memory for batch operations.")
        return self._held_model

    def release_model(self):
        if self._held_model is not None:
            self._destroy_model(self._held_model)
            self._held_model = None
            logger.info("Held model released.")

    def generate(self, prompt, stream: bool = True):
        use_held = self._held_model is not None

        if stream:
            streamer = YieldingTextStreamer(self.tokenizer)

            def run_generation():
                model = None
                should_destroy = False
                try:
                    with audio_pipeline_lock:
                        if use_held:
                            model = self._held_model
                        else:
                            model = self._load_model()
                            should_destroy = True
                        model.generate(
                            prompt,
                            streamer=streamer,
                            max_new_tokens=config.models.summarizer.max_new_tokens,
                            temperature=self.temperature,
                            do_sample=False,
                        )

                except Exception as e:
                    error_msg = "Summary generation failed. Please ensure sufficient free resources are available to run this process."
                    logger.error(f"Exception occured in summary generation")
                    if "out of gpu resources" in str(e).lower():
                        error_msg = "Summary generation failed. Insufficient GPU resources available to run this process."
                    streamer._queue.put(f"[ERROR]: {error_msg}")
                finally:
                    if should_destroy and model is not None:
                        self._destroy_model(model)
                    streamer.end()

            threading.Thread(target=run_generation, daemon=True).start()
            return streamer
        else:
            model = None
            should_destroy = False
            try:
                with audio_pipeline_lock:
                    if use_held:
                        model = self._held_model
                    else:
                        model = self._load_model()
                        should_destroy = True
                    return model.generate(
                        prompt,
                        max_new_tokens=config.models.summarizer.max_new_tokens,
                        temperature=self.temperature,
                        do_sample=False,
                    )
            finally:
                if should_destroy and model is not None:
                    self._destroy_model(model)

    def _load_model(self):
        logger.info("Loading model instance...")
        return ov_genai.LLMPipeline(ensure_model.get_model_path(), device=self.device)

    def _destroy_model(self, model):
        try:
            del model
            gc.collect()
            import time
            time.sleep(1)
            logger.info("Model instance destroyed and memory reclaimed")
        except Exception as e:
            logger.warning(f"Failed to fully destroy model: {e}")
