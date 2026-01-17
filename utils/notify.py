import logging
import requests
from abc import ABC, abstractmethod


class Notifier(ABC):

    @abstractmethod
    def send(self, title: str, message: str):
        pass


class NtfyNotifier(Notifier):

    def __init__(self, url):
        self.url = url

    def send(self, title: str, message: str):
        try:
            response = requests.post(self.url, data=message.encode(encoding='utf-8'), headers={"Title": title.encode(encoding='utf-8')})
            response.raise_for_status()
            logging.info(f"Ntfy notification sent: {title}")
        except Exception as e:
            logging.error(f"Failed to send ntfy notification: {e}")


def create_notifiers(config_list):
    notifiers = []
    for cfg in config_list:
        if cfg.get("type") == "ntfy":
            notifiers.append(NtfyNotifier(cfg.get("url")))
    return notifiers


def send_notifications(notifiers, title, message):
    if not notifiers:
        return
    for notifier in notifiers:
        notifier.send(title, message)
