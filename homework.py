from __future__ import annotations

import http
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from exceptions import (
    APIRequestError,
    APIResponseFormatError,
)

from dotenv import load_dotenv


load_dotenv()

PRACTICUM_TOKEN: Optional[str] = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN: Optional[str] = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID: Optional[str] = os.getenv('TELEGRAM_CHAT_ID')


RETRY_PERIOD: int = 600
RETRY_TIME: int = RETRY_PERIOD
ENDPOINT: str = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'

HEADERS: Dict[str, str] = {
    'Authorization': f'OAuth {PRACTICUM_TOKEN}'
}

HOMEWORK_VERDICTS: Dict[str, str] = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(stream=sys.stdout)
_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
_handler.setFormatter(_formatter)
logger.addHandler(_handler)
logger.propagate = True


def check_tokens() -> bool:
    """Проверить наличие необходимых переменных окружения."""
    required_tokens = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }
    all_present = True
    for name, value in required_tokens.items():
        if not value:
            logger.critical(
                "Отсутствует обязательная переменная окружения: '%s'",
                name,
            )
            all_present = False
    return all_present


def send_message(bot: Any, message: str) -> None:
    """Отправить сообщение через бота Telegram и залогировать результат."""
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Бот отправил сообщение "%s"', message)
    except Exception as error:
        logger.error('Сбой при отправке сообщения: %s', error)


def get_api_answer(current_timestamp: int) -> Dict[str, Any]:
    """Отправить запрос к API Practicum и вернуть ответ."""
    headers = HEADERS or {}
    params = {'from_date': current_timestamp}
    try:
        response = requests.get(ENDPOINT, headers=headers, params=params)
    except requests.RequestException as error:
        logger.error('Ошибка при запросе к API: %s', error)
        raise APIRequestError(ENDPOINT) from error
    if response.status_code != http.HTTPStatus.OK:
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
    """Проверить структуру ответа API и вернуть список домашних работ."""
    if not isinstance(response, dict):
        raise TypeError('Ответ API не является словарём')
    if 'homeworks' not in response:
        raise KeyError('Missing key "homeworks" in API response')
    if 'current_date' not in response:
        raise KeyError('Missing key "current_date" in API response')
    homeworks = response['homeworks']
    current_date = response['current_date']
    if not isinstance(homeworks, list):
        raise TypeError('homeworks is not a list')
    if not isinstance(current_date, (int, float)):
        raise TypeError('current_date has invalid type')
    return homeworks


def parse_status(homework: Dict[str, Any]) -> str:
    """Получить статус домашней работы и сформировать сообщение."""
    if not isinstance(homework, dict):
        raise TypeError('homework is not a dict')
    if 'homework_name' not in homework:
        raise KeyError('Missing key "homework_name" in homework info')
    if 'status' not in homework:
        raise KeyError('Missing key "status" in homework info')
    homework_name = homework['homework_name']
    status = homework['status']
    if status not in HOMEWORK_VERDICTS:
        logger.error('Недокументированный статус домашней работы: %s', status)
        raise KeyError(f'Unexpected status "{status}" in homework info')
    verdict = HOMEWORK_VERDICTS[status]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def process_updates(
    bot: Any,
    current_timestamp: int,
    prev_status_msg: str,
    prev_error_msg: str,
) -> Tuple[int, str, str]:
    """Запросить обновления и отправить уведомления.

    Эта функция инкапсулирует логику получения ответа от API,
    проверки его структуры, формирование сообщений и отправку их в
    Telegram. Она возвращает обновлённые значения временной метки и
    последних отправленных сообщений, чтобы `main` могла продолжить
    работу.
    """
    try:
        response = get_api_answer(current_timestamp)
        homeworks = check_response(response)
        if homeworks:
            status_message = parse_status(homeworks[0])
            if status_message != prev_status_msg:
                send_message(bot, status_message)
                prev_status_msg = status_message
            else:
                logger.debug('Статус домашней работы не изменился.')
        else:
            logger.debug('В ответе нет новых статусов.')
        current_timestamp = int(
            response.get('current_date', current_timestamp)
        )
    except Exception as error:
        error_message = f'Сбой в работе программы: {error}'
        logger.error(error_message)
        if error_message != prev_error_msg:
            try:
                send_message(bot, error_message)
                prev_error_msg = error_message
            except Exception:
                prev_error_msg = error_message
    return current_timestamp, prev_status_msg, prev_error_msg


def main() -> None:
    """Запустить бота для проверки домашних работ."""
    if not check_tokens():
        logger.critical(
            '''Программа принудительно остановлена из-за
            отсутствия переменных окружения.''')
        raise SystemExit('Missing required environment variables')
    try:
        telebot_module = sys.modules.get('telebot')
        if telebot_module is None:
            import telebot  # type: ignore
        else:
            telebot = telebot_module  # type: ignore
    except Exception as error:
        logger.critical(
            'Не удалось импортировать класс TeleBot из модуля telebot: %s',
            error,
        )
        raise SystemExit(
            'Telegram library isrequired to run the bot') from error
    bot = telebot.TeleBot(token=TELEGRAM_TOKEN)  # type: ignore[name-defined]
    current_timestamp: int = int(time.time())
    previous_status_message: str = ''
    previous_error_message: str = ''
    while True:
        (current_timestamp,
         previous_status_message,
         previous_error_message) = process_updates(
            bot,
            current_timestamp,
            previous_status_message,
            previous_error_message,
        )
        time.sleep(RETRY_TIME)
