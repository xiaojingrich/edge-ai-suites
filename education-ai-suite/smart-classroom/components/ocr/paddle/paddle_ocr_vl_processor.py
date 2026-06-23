import logging
from dataclasses import dataclass, field
from typing import List, Optional
from PIL import Image

from components.ocr.base_ocr import BaseOCR
from utils.config_loader import config

logger = logging.getLogger(__name__)


@dataclass
class ImageBlock:
    """Represents a detected image region in the document."""
    index: int
    coordinate: List[float]  # [x1, y1, x2, y2]
    label: str  # e.g. "image", "chart", "figure_title"
    score: float
    description: Optional[str] = None  # VLM-generated description, None if disabled


@dataclass
class VLOCRResult:
    """Structured result from PaddleOCR-VL processing."""
    markdown: str
    image_blocks: List[ImageBlock] = field(default_factory=list)
    has_images: bool = False


class PaddleOCRVLProcessor(BaseOCR):
    """PaddleOCR-VL-1.6 processor for structured document parsing.

    Outputs structured Markdown with layout detection. Can optionally
    describe detected image regions via an external VLM.
    """
    _model = None
    _config = None

    def __init__(
        self,
        lang=None,
        use_angle_cls: bool = True,
        device=None,
        vlm_enabled: bool = False,
        vlm_describe_fn=None,
    ):
        lang = lang or config.app.language
        device = device or config.models.ocr.device
        super().__init__(lang, use_angle_cls, device)

        self.vlm_enabled = vlm_enabled
        self.vlm_describe_fn = vlm_describe_fn

        model_config_key = (lang, device, "vl-1.6")

        if PaddleOCRVLProcessor._model is None or PaddleOCRVLProcessor._config != model_config_key:
            logger.info("Loading PaddleOCR-VL-1.6 model...")
            from paddleocr import PaddleOCRVL
            import os
            os.environ.setdefault("GLOG_minloglevel", "2")

            vl_config = getattr(config.models.ocr, "vl", None)
            kwargs = {}
            if vl_config:
                if getattr(vl_config, "model_dir", None):
                    kwargs["vl_rec_model_dir"] = vl_config.model_dir
                if getattr(vl_config, "backend", None):
                    kwargs["vl_rec_backend"] = vl_config.backend
                if getattr(vl_config, "server_url", None):
                    kwargs["vl_rec_server_url"] = vl_config.server_url

            PaddleOCRVLProcessor._model = PaddleOCRVL(
                pipeline_version="v1.6",
                use_doc_orientation_classify=True,
                use_doc_unwarping=True,
                use_layout_detection=True,
                use_chart_recognition=True,
                use_ocr_for_image_block=True,
                **kwargs,
            )
            PaddleOCRVLProcessor._config = model_config_key
            logger.info("PaddleOCR-VL-1.6 model loaded")

        self.vl_model = PaddleOCRVLProcessor._model

    def ocr(self, file_path: str) -> List[List]:
        results = self.vl_model.predict(file_path)
        return results

    def extract_text(self, file_path: str) -> str:
        """Extract text as structured Markdown."""
        result = self.extract_structured(file_path)
        return result.markdown

    def extract_structured(self, file_path: str) -> VLOCRResult:
        """Extract structured result with Markdown and image block info."""
        results = self.vl_model.predict(file_path)

        if not results:
            return VLOCRResult(markdown="", image_blocks=[], has_images=False)

        markdown_pages = []
        all_image_blocks = []
        block_index = 0

        for page_result in results:
            res = page_result

            page_markdown = self._extract_markdown(res)
            markdown_pages.append(page_markdown)

            layout_res = self._get_layout_result(res)
            if layout_res:
                for box in layout_res.get("boxes", []):
                    if box.get("label") in ("image", "chart", "figure_title"):
                        img_block = ImageBlock(
                            index=block_index,
                            coordinate=box["coordinate"],
                            label=box["label"],
                            score=box.get("score", 0.0),
                        )
                        if self.vlm_enabled and self.vlm_describe_fn:
                            img_block.description = self._describe_image_block(
                                file_path, box["coordinate"]
                            )
                        all_image_blocks.append(img_block)
                        block_index += 1

        markdown = "\n\n".join(markdown_pages)

        if all_image_blocks and self.vlm_enabled:
            markdown = self._insert_image_descriptions(markdown, all_image_blocks)

        return VLOCRResult(
            markdown=markdown,
            image_blocks=all_image_blocks,
            has_images=len(all_image_blocks) > 0,
        )

    def _extract_markdown(self, res) -> str:
        """Extract markdown content from a page result."""
        md = None
        if hasattr(res, "markdown"):
            md = res.markdown
        elif isinstance(res, dict):
            if "markdown" in res:
                md = res["markdown"]
            else:
                lp = res.get("layout_parsing_result")
                if lp:
                    if hasattr(lp, "markdown"):
                        md = lp.markdown
                    elif isinstance(lp, dict) and "markdown" in lp:
                        md = lp["markdown"]

        if md is None:
            return ""
        if isinstance(md, str):
            return md
        if isinstance(md, dict):
            return md.get("markdown_texts", md.get("markdown_text", ""))
        return str(md)

    def _get_layout_result(self, res) -> Optional[dict]:
        """Extract layout detection result from a page result."""
        if isinstance(res, dict):
            layout = res.get("layout_det_res")
            if layout:
                return layout if isinstance(layout, dict) else vars(layout) if hasattr(layout, "__dict__") else None
            lp = res.get("layout_parsing_result")
            if lp and isinstance(lp, dict):
                return lp.get("layout_det_res")
        if hasattr(res, "layout_det_res"):
            ld = res.layout_det_res
            return ld if isinstance(ld, dict) else vars(ld) if hasattr(ld, "__dict__") else None
        return None

    def _describe_image_block(self, file_path: str, coordinate: List[float]) -> Optional[str]:
        """Crop image block and get VLM description."""
        try:
            img = Image.open(file_path)
            x1, y1, x2, y2 = [int(c) for c in coordinate]
            cropped = img.crop((x1, y1, x2, y2))
            return self.vlm_describe_fn(cropped)
        except Exception as e:
            logger.warning(f"Failed to describe image block: {e}")
            return None

    def _insert_image_descriptions(self, markdown: str, image_blocks: List[ImageBlock]) -> str:
        """Append image descriptions to the markdown."""
        descriptions = []
        for block in image_blocks:
            if block.description:
                descriptions.append(
                    f"[图片区域 {block.index + 1} ({block.label}): {block.description}]"
                )
            else:
                descriptions.append(
                    f"[图片区域 {block.index + 1} ({block.label}): 需人工查看]"
                )

        if descriptions:
            markdown += "\n\n---\n### 图片区域描述\n" + "\n".join(descriptions)

        return markdown
