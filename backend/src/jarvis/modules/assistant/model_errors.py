"""Falhas públicas da conversa. Nenhum diagnóstico incorpora mensagens do provedor."""

from enum import Enum


class ModelIssue(Enum):
    UNAVAILABLE = (
        "model_unavailable",
        "Não consegui consultar o modelo de conversa para responder. Tente novamente mais tarde.",
        True,
    )
    CONFIGURATION = (
        "model_configuration",
        "Confira o provedor e o modelo de conversa no .env do Jarvis e reinicie o serviço.",
        False,
    )
    KEY_MISSING = (
        "model_key_missing",
        "Falta configurar a chave de conversa no .env do Jarvis. Use OPENAI_API_KEY para OpenAI ou GROQ_API_KEY para Groq e reinicie o serviço.",
        False,
    )
    MODEL_MISSING = (
        "model_name_missing",
        "Confira o nome do modelo no .env do Jarvis. Use OPENAI_MODEL para OpenAI ou GROQ_MODEL para Groq e reinicie o serviço.",
        False,
    )
    ACCESS_DENIED = (
        "model_access_denied",
        "O provedor recusou o acesso à IA de conversa. Confira se a chave está ativa e se tem permissão para usar o modelo escolhido.",
        False,
    )
    LOCAL_ACCESS = (
        "ollama_access_denied",
        "O serviço local recusou o acesso. Confira se OLLAMA_BASE_URL aponta para o Ollama e se o Jarvis tem permissão para usar a API de conversa.",
        False,
    )
    LOCAL_MODEL = (
        "ollama_model_not_found",
        "O serviço local respondeu que o recurso não foi encontrado. Confira se OLLAMA_BASE_URL aponta para o Ollama e se OLLAMA_MODEL aparece em ollama list.",
        False,
    )
    HOSTED_MODEL = (
        "hosted_model_not_found",
        "O provedor não encontrou o modelo de conversa para esta conta. Confira o nome configurado e se sua conta tem acesso a ele.",
        False,
    )
    LOCAL_CONNECTION = (
        "ollama_connection",
        "Não consegui conectar ao Ollama. Confira se ele está rodando e se OLLAMA_BASE_URL aponta para a porta correta.",
        True,
    )
    REQUEST_REJECTED = (
        "model_request_rejected",
        "O serviço de IA recusou a solicitação. Confira se o modelo escolhido aceita a API de conversa usada pelo Jarvis.",
        False,
    )
    RATE_LIMIT = (
        "model_rate_limited",
        "O provedor limitou as chamadas de conversa. Confira a cota da sua conta e tente novamente mais tarde.",
        True,
    )

    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable


class ModelUnavailable(Exception):
    def __init__(self, issue: ModelIssue | None = None, *, retryable: bool = True):
        self.issue = issue or (ModelIssue.UNAVAILABLE if retryable else ModelIssue.CONFIGURATION)
        self.retryable = self.issue.retryable
        super().__init__(self.issue.message)

    @classmethod
    def for_http(cls, status: int, *, local: bool):
        if status == 404:
            return cls(ModelIssue.LOCAL_MODEL if local else ModelIssue.HOSTED_MODEL)
        if status in (401, 403):
            return cls(ModelIssue.LOCAL_ACCESS if local else ModelIssue.ACCESS_DENIED)
        if status == 429:
            return cls(ModelIssue.RATE_LIMIT)
        if status in (408, 425) or status >= 500:
            return cls(ModelIssue.UNAVAILABLE)
        return cls(ModelIssue.REQUEST_REJECTED)
