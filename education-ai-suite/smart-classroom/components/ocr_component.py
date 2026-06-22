from components.base_component import PipelineComponent
from components.ocr.paddle.paddle_ocr_processor import PaddleOCRProcessor
from components.ocr.openvino.openvino_ocr_processor import OpenVINOOCRProcessor
from utils.config_loader import config
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class OCRComponent(PipelineComponent):

    _model = None
    _config = None

    def __init__(self, session_id, provider="openvino-paddle", lang="en", device="CPU",
                 vlm_enabled=False, vlm_describe_fn=None):
        self.session_id = session_id
        self.provider = provider.lower()
        self.lang = lang
        self.device = device
        self.vlm_enabled = vlm_enabled
        self.vlm_describe_fn = vlm_describe_fn
        model_config_key = (self.provider, self.lang, self.device, vlm_enabled)

        if OCRComponent._model is None or OCRComponent._config != model_config_key:
            try:
                logger.debug(f"Initializing OCR component: provider={self.provider}, lang={self.lang}, device={self.device}")
                if self.provider == "native":
                    logger.info("Ensuring PaddleOCR models are cached...")
                    OCRComponent._model = PaddleOCRProcessor(lang=self.lang,use_angle_cls=True,device=self.device)
                elif self.provider == "openvino":
                    OCRComponent._model = OpenVINOOCRProcessor(
                        lang=self.lang,
                        use_angle_cls=True,
                        device=self.device,
                        ir_models_dir=config.models.ocr.model_dir
                    )
                elif self.provider == "paddleocr-vl":
                    from components.ocr.paddle.paddle_ocr_vl_processor import PaddleOCRVLProcessor
                    OCRComponent._model = PaddleOCRVLProcessor(
                        lang=self.lang,
                        use_angle_cls=True,
                        device=self.device,
                        vlm_enabled=vlm_enabled,
                        vlm_describe_fn=vlm_describe_fn,
                    )
                else:
                    raise ValueError(f"Unsupported OCR provider: {self.provider}")

                OCRComponent._config = model_config_key
                logger.info(f"OCR model initialized: provider={self.provider}, lang={self.lang}, device={self.device}")
            except Exception as e:
                logger.error(f" Failed to initialize OCR model: {e}")
                raise

        self.ocr_model = OCRComponent._model



    def process(self, input_data):
        """Process OCR request with cached model."""
        pass
