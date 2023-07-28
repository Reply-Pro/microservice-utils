import typing
from uuid import UUID

from novu.api import EventApi, SubscriberApi
from novu.dto import SubscriberDto


class Notifier:
    def __init__(self, api_key, base_url="https://api.novu.co"):
        self.event_api = EventApi(base_url, api_key)

    def send_notification(
        self, name, users: list[UUID], context: dict[str, typing.Any], **kwargs
    ):
        self.event_api.trigger(
            name=name,  # This is the slug of the workflow name.
            recipients=[str(u) for u in users],
            payload=context,
        )


class SubscriberManager:
    def __init__(self, api_key, base_url="https://api.novu.co"):
        self.subscriber_api = SubscriberApi(base_url, api_key)

    def subscribe_user(
        self,
        user: UUID,
        email: str,
        first_name: str = None,
        last_name: str = None,
        phone: str = None,
        **kwargs
    ):
        dto = SubscriberDto(
            subscriber_id=str(user),
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            **kwargs
        )
        self.subscriber_api.create(dto)

    def unsubscribe_user(self, user: UUID):
        self.subscriber_api.delete(str(user))
