class DomainError(Exception):
    def __init__(self, code, message, status=400, actions=None):
        self.code = code; self.message = message; self.status = status
        self.actions = actions or []
        super().__init__(code)

def missing():
    raise DomainError("NOT_FOUND", "资源不存在或无权访问", 404)
