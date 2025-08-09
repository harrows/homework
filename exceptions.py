from typing import Optional


class HomeworkBotError(Exception):
    """Base class for all custom exceptions raised by the homework bot."""


class MissingTokenError(HomeworkBotError):
    """Raised when a required environment variable is missing."""

    def __init__(self, token_name: str) -> None:
        message = f"Отсутствует обязательная переменная окружения: '{token_name}'"
        super().__init__(message)
        self.token_name = token_name


class APIRequestError(HomeworkBotError):
    """Raised when there is an error making a request to the Practicum API."""

    def __init__(self, endpoint: str, status_code: Optional[int] = None) -> None:
        code_str = f" Код ответа API: {status_code}" if status_code is not None else ""
        message = (
            f"Сбой в работе программы: Эндпоинт {endpoint} недоступен.{code_str}"
        )
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code


class APIResponseFormatError(HomeworkBotError):
    """Raised when the API response does not match the expected format."""

    def __init__(self, detail: str) -> None:
        message = f"Некорректный формат ответа API: {detail}"
        super().__init__(message)
        self.detail = detail


class UnknownStatusError(HomeworkBotError):
    """Raised when an unexpected homework status is encountered."""

    def __init__(self, status: str) -> None:
        message = f"Недокументированный статус домашней работы: '{status}'"
        super().__init__(message)
        self.status = status