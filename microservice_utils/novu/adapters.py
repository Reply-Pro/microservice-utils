import typing
from dataclasses import dataclass
from uuid import UUID

from novu_py import Novu, TriggerEventRequestDto


@dataclass
class ActivityNotificationSubscriberResponseDTO:
    _id: str
    email: str
    first_name: str
    last_name: str
    phone: str


@dataclass
class ActivityNotificationTriggerResponseDto:
    type: str
    identifier: str
    variables: typing.List[dict]


@dataclass
class ActivityNotificationTemplateResponseDto:
    name: str
    triggers: typing.List[ActivityNotificationTriggerResponseDto]
    _id: str


@dataclass
class ActivityNotificationDto:
    _environment_id: str
    _organization_id: str
    transaction_id: str
    created_at: str
    channels: typing.List[str]
    subscriber: ActivityNotificationSubscriberResponseDTO
    template: ActivityNotificationTemplateResponseDto
    jobs: typing.List[dict]
    _subscriber: str


@dataclass
class NotificationResponse:
    page: int
    has_more: bool
    page_size: int
    _data: typing.List[ActivityNotificationDto]


class Notifier:
    def __init__(self, api_key):
        self.api = Novu(api_key)

    def send_notification(
        self,
        name,
        users: list[UUID],
        context: dict[str, typing.Any],
        overrides: typing.Optional[dict[str, typing.Any]] = None,
        **kwargs,
    ):
        self.api.trigger(
            trigger_event_request_dto=TriggerEventRequestDto(
                name=name,  # This is the slug of the workflow name.
                recipients=[str(u) for u in users],
                payload=context,
                overrides=overrides if overrides else None,
            )
        )

    def get_notifications(self, page: int) -> NotificationResponse:
        notifications = self.api.notifications.list(request={"page": page})

        page = notifications.page
        has_more = notifications.has_more
        page_size = notifications.page_size
        data = notifications._data

        return NotificationResponse(
            page=page, has_more=has_more, page_size=page_size, _data=data
        )


class SubscriberManager:
    def __init__(self, api_key):
        self.api = Novu(api_key)

    @staticmethod
    def build_collaborator_id(
        identifier: str, prefix: str = "nonusercollaborator"
    ) -> str:
        """Use this method to build a collaborator identifier"""
        if not prefix:
            raise ValueError("Prefix expected")

        return f"{prefix}:{identifier}"

    def _subscribe(
        self,
        id_: str,
        email: str,
        first_name: typing.Optional[str] = None,
        last_name: typing.Optional[str] = None,
        phone: typing.Optional[str] = None,
        **kwargs,
    ):
        self.api.subscribers.create(
            create_subscriber_request_dto={
                "subscriber_id": id_,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                **kwargs,
            }
        )

    def _unsubscribe(self, id_: str):
        self.api.subscribers.delete(subscriber_id=id_)

    def subscribe_collaborator(
        self,
        identifier: str,
        email: str,
        first_name: typing.Optional[str] = None,
        last_name: typing.Optional[str] = None,
        phone: typing.Optional[str] = None,
        **kwargs,
    ):
        self._subscribe(
            identifier,
            email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            **kwargs,
        )

    def subscribe_user(
        self,
        user: UUID,
        email: str,
        first_name: typing.Optional[str] = None,
        last_name: typing.Optional[str] = None,
        phone: typing.Optional[str] = None,
        **kwargs,
    ):
        self._subscribe(
            str(user),
            email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            **kwargs,
        )

    def unsubscribe_collaborator(self, identifier: str):
        self._unsubscribe(identifier)

    def unsubscribe_user(self, user: UUID):
        self._unsubscribe(str(user))
