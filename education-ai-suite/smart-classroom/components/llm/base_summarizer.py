class BaseSummarizer:
    def __init__(self, model_name=..., device="CPU", revision=None):
       raise NotImplementedError

    def generate(self, prompt: str) -> str:
        raise NotImplementedError

    def acquire_model(self):
        """Load and hold the model in memory. Call release_model() when done."""
        raise NotImplementedError

    def release_model(self):
        """Release the model held by acquire_model()."""
        raise NotImplementedError