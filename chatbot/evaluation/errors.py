class EvaluationStopped(RuntimeError):
    """Safe reason codes only. Never include provider errors, keys or arbitrary text."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)
