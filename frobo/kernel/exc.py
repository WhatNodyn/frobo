class ProcessTerminated(BaseException):
    def __init__(self, code: int):
        super().__init__(f'Process terminated with code {code}', code)
        self.code = code

class BadModule(BaseException):
    pass