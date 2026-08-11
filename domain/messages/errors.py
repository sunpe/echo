"""echo Codex integration error types."""


class CodexError(RuntimeError):
    pass


class CodexConfigurationError(CodexError):
    pass


class CodexConnectionError(CodexError):
    pass


class CodexCompatibilityError(CodexError):
    pass


class CodexRPCError(CodexError):
    def __init__(self, method, error):
        self.method = method
        self.error = error
        if isinstance(error, dict):
            message = error.get("message") or str(error)
            self.code = error.get("code")
        else:
            message = str(error)
            self.code = None
        super().__init__("{} failed: {}".format(method, message))


class CodexRPCTimeout(CodexError):
    pass
