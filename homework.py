from __future__ import annotations

import http
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

from .exceptions import (
    APIRequestError,
    APIResponseFormatError,
    MissingTokenError,
    UnknownStatusError,
)


# Load environment variables. These names must not be changed because
# external tests rely on them.
PRACTICUM_TOKEN: Optional[str] = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN: Optional[str] = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID: Optional[str] = os.getenv('TELEGRAM_CHAT_ID')

# Constants defining behaviour of the bot.
RETRY_TIME: int = 600  # interval between API requests in seconds
ENDPOINT: str = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'

# Authorization header for Practicum API requests. It is defined lazily
# because PRACTICUM_TOKEN may be absent; the header is reconstructed in
# ``get_api_answer`` when the token is present.
HEADERS: Dict[str, str] = {}

# Mapping of homework status codes returned by the API to human‑readable
# messages. When adding new statuses here, ensure that the tests are
# updated accordingly.
HOMEWORK_VERDICTS: Dict[str, str] = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}

# Configure logging to output to STDOUT with a consistent format.
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(stream=sys.stdout)
_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s'
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)
logger.propagate = False


def check_tokens() -> bool:
    """Check that all required environment variables are set.

    Returns ``True`` if all tokens are present and non‑empty; otherwise
    logs a critical error for each missing token and returns ``False``.

    :returns: bool indicating whether all tokens are present.
    """
    required_tokens = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }
    all_present = True
    for name, value in required_tokens.items():
        if not value:
            # Log and mark that at least one token is missing. Do not
            # immediately raise here to allow checking of all variables.
            logger.critical(
                "Отсутствует обязательная переменная окружения: '%s'",
                name,
            )
            all_present = False
    return all_present


def send_message(bot: Any, message: str) -> None:
    """Send a message via a Telegram bot and log the result.

    Uses the provided bot object's ``send_message`` method to send the
    ``message`` to the chat identified by ``TELEGRAM_CHAT_ID``. Logs
    both successes and failures. If an exception occurs during
    sending, it is re‑raised so that the caller can decide how to
    proceed (for example, whether to retry or suppress further
    notifications).

    :param bot: An object implementing ``send_message(chat_id, text)``.
    :param message: The message text to be sent.
    """
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.debug('Бот отправил сообщение: "%s"', message)
    except Exception as error:
        logger.error('Сбой при отправке сообщения: %s', error)
        # Propagate the error to allow the caller to handle it.
        raise


def get_api_answer(current_timestamp: int) -> Dict[str, Any]:
    """Request homework statuses from the Practicum API.

    Constructs a GET request to the Practicum API endpoint using the
    provided timestamp as the ``from_date`` parameter. If the request
    succeeds and returns status code 200, the JSON response is
    converted to native Python types and returned. Otherwise, a
    descriptive ``APIRequestError`` is raised.

    :param current_timestamp: Epoch timestamp (in seconds) from which
        the API should return homework updates.
    :returns: The parsed JSON response as a dictionary.
    :raises APIRequestError: If the HTTP status is not OK or the
        endpoint cannot be reached.
    :raises APIResponseFormatError: If the response body cannot be
        decoded as JSON.
    """
    # Rebuild headers on each call in case PRACTICUM_TOKEN changes during
    # runtime (for example in tests). If the token is missing, the
    # resulting header will be empty and the request will fail with
    # appropriate error handling below.
    headers = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'} if PRACTICUM_TOKEN else {}
    params = {'from_date': current_timestamp}
    try:
        response = requests.get(ENDPOINT, headers=headers, params=params)
    except requests.RequestException as error:
        logger.error('Ошибка при запросе к API: %s', error)
        # Wrap the underlying exception in a custom exception so that
        # callers can differentiate network errors from logical ones.
        raise APIRequestError(ENDPOINT) from error

    if response.status_code != http.HTTPStatus.OK:
        # Log before raising so that the reason is recorded even if
        # callers choose not to log this particular exception again.
        logger.error(
            'Эндпоинт %s недоступен. Код ответа API: %s',
            ENDPOINT,
            response.status_code,
        )
        raise APIRequestError(ENDPOINT, response.status_code)

    try:
        return response.json()
    except json.JSONDecodeError as error:
        logger.error('Не удалось декодировать JSON из ответа API: %s', error)
        raise APIResponseFormatError('Невозможно декодировать JSON') from error


def check_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate the structure of the Practicum API response.

    Ensures that ``response`` is a dictionary containing the keys
    ``homeworks`` (which must map to a list) and ``current_date``.
    Raises ``APIResponseFormatError`` if the structure does not match
    expectations. Returns the list of homework entries for further
    processing.

    :param response: The decoded API response.
    :returns: A list of homework dictionaries extracted from the
        response.
    :raises APIResponseFormatError: If the response structure is
        invalid.
    """
    if not isinstance(response, dict):
        raise APIResponseFormatError('Ответ API не является словарём')

    if 'homeworks' not in response:
        raise APIResponseFormatError("В ответе API отсутствует ключ 'homeworks'")
    homeworks = response['homeworks']
    if not isinstance(homeworks, list):
        raise APIResponseFormatError("Значение 'homeworks' не является списком")

    # 'current_date' presence is also expected as per API documentation.
    if 'current_date' not in response:
        raise APIResponseFormatError("В ответе API отсутствует ключ 'current_date'")
    # Ensure the current_date is either int or float (epoch timestamp). We
    # don't explicitly use the value here but verifying its type helps
    # catch unexpected changes in the API contract early.
    current_date = response['current_date']
    if not isinstance(current_date, (int, float)):
        raise APIResponseFormatError("Значение 'current_date' имеет неверный тип")

    return homeworks


def parse_status(homework: Dict[str, Any]) -> str:
    """Derive a status message for a single homework entry.

    Extracts the homework name and status from the supplied dictionary.
    Uses the global ``HOMEWORK_VERDICTS`` mapping to translate the
    status code into a human‑readable verdict. If either the name or
    status is missing or if the status is unexpected, an exception is
    raised.

    :param homework: A dictionary representing a single homework entry.
    :returns: A formatted message describing the change in homework
        status.
    :raises APIResponseFormatError: If required keys are missing from
        ``homework``.
    :raises UnknownStatusError: If the status code is not defined in
        ``HOMEWORK_VERDICTS``.
    """
    if not isinstance(homework, dict):
        raise APIResponseFormatError('Элемент списка homeworks не является словарём')

    if 'homework_name' not in homework:
        raise APIResponseFormatError("В информации о домашней работе отсутствует ключ 'homework_name'")
    if 'status' not in homework:
        raise APIResponseFormatError("В информации о домашней работе отсутствует ключ 'status'")

    homework_name = homework['homework_name']
    status = homework['status']
    if status not in HOMEWORK_VERDICTS:
        raise UnknownStatusError(status)
    verdict = HOMEWORK_VERDICTS[status]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def main() -> None:
    """Run the Practicum homework bot.

    The main loop of the bot performs the following actions:

    1. Verify that all required environment variables are set.
    2. Instantiate a Telegram bot using the provided token.
    3. Initialize the timestamp for polling the Practicum API.
    4. Enter an infinite loop in which it:
        a. Fetches homework updates from the API.
        b. Validates and parses the response.
        c. Sends notifications about new homework statuses.
        d. Handles and reports any exceptions that occur.
        e. Sleeps for ``RETRY_TIME`` seconds before the next iteration.

    On encountering missing environment variables or an unrecoverable
    error during bot instantiation, the function logs a critical
    message and terminates the process using ``SystemExit``.
    """
    if not check_tokens():
        # Terminate immediately if any required tokens are absent. This
        # prevents the bot from running in an invalid state.
        logger.critical('Программа принудительно остановлена из-за отсутствия переменных окружения.')
        raise SystemExit('Missing required environment variables')

    # Import telegram.Bot lazily to avoid ImportError at module import
    # time if the package is not installed. Tests may patch
    # telegram.Bot dynamically, so importing here ensures that the
    # patched version is picked up when main() runs.
    try:
        from telegram import Bot  # type: ignore
    except Exception as error:
        logger.critical('Не удалось импортировать класс Bot из модуля telegram: %s', error)
        raise SystemExit('Telegram library is required to run the bot') from error

    bot = Bot(token=TELEGRAM_TOKEN)

    current_timestamp: int = int(time.time())
    previous_status_message: str = ''
    previous_error_message: str = ''

    while True:
        try:
            response = get_api_answer(current_timestamp)
            homeworks = check_response(response)
            if homeworks:
                # Process only the first homework in the list as per the
                # Practicum API spec. If multiple homeworks are present,
                # subsequent statuses will be picked up on the next
                # iteration.
                status_message = parse_status(homeworks[0])
                if status_message != previous_status_message:
                    send_message(bot, status_message)
                    previous_status_message = status_message
                else:
                    logger.debug('Статус домашней работы не изменился.')
            else:
                logger.debug('В ответе нет новых статусов.')
            current_timestamp = int(response.get('current_date', current_timestamp))
        except Exception as error:
            # Compose a descriptive error message for logging and
            # notification. Many different exceptions can occur here,
            # including our custom exceptions and standard library
            # exceptions. Converting to str yields a concise
            # representation suitable for end users.
            error_message = f'Сбой в работе программы: {error}'
            logger.error(error_message)
            # Notify the user about the error only if it's different from
            # the last reported error to avoid spamming. Do not
            # attempt to send another message if sending fails.
            if error_message != previous_error_message:
                try:
                    send_message(bot, error_message)
                    previous_error_message = error_message
                except Exception:
                    # Even if sending the error message fails, we
                    # remember it to avoid repeating further attempts.
                    previous_error_message = error_message
        finally:
            time.sleep(RETRY_TIME)