import logging
import os
import threading
import typing
from dataclasses import dataclass
from uuid import UUID

import httpx
from novu_py import Novu, TriggerEventRequestDto, BulkTriggerEventDto
from novu_py.models import ResponseValidationError

from microservice_utils.novu.mirror import NovuMirror

logger = logging.getLogger(__name__)

#: Copy every write Novu accepts to this Novu-compatible endpoint (see ``mirror.py``).
#: Both must be set; without them nothing changes.
MIRROR_URL_ENV = "NOVU_MIRROR_URL"
MIRROR_API_KEY_ENV = "NOVU_MIRROR_API_KEY"

_mirrors: dict[tuple[str, str], NovuMirror] = {}
_http_clients: dict[int, httpx.Client] = {}
_lock = threading.Lock()


def _novu_transport() -> typing.Optional[httpx.BaseTransport]:
    """None: httpx chooses the transport, as it does for the SDK, proxies included.
    Tests put a fake Novu here."""
    return None


def mirror_from_settings(
    url: typing.Optional[str] = None, api_key: typing.Optional[str] = None
) -> typing.Optional[NovuMirror]:
    """The process's mirror; None when off.

    The URL and the key come together: both from the arguments when either is given,
    else both from the environment. A pair is never mixed, so a URL passed in code
    can never receive the key the environment holds for another endpoint.
    """
    if url is not None or api_key is not None:
        url, api_key = (url or "").strip(), (api_key or "").strip()
    else:
        url = os.environ.get(MIRROR_URL_ENV, "").strip()
        api_key = os.environ.get(MIRROR_API_KEY_ENV, "").strip()
    if not (url and api_key):
        return None
    with _lock:
        if (url, api_key) not in _mirrors:
            _mirrors[(url, api_key)] = NovuMirror(url, api_key)
        return _mirrors[(url, api_key)]


def _novu_client(
    api_key: str,
    mirror_url: typing.Optional[str] = None,
    mirror_api_key: typing.Optional[str] = None,
) -> Novu:
    mirror = mirror_from_settings(mirror_url, mirror_api_key)
    if mirror is None:
        return Novu(api_key)
    # The SDK never closes a client it is given, and services build a Novu client per
    # injection: every client of the process shares this one.
    with _lock:
        client = _http_clients.get(id(mirror))
        if client is None:
            # The same client the SDK would build, plus the mirror's response hook.
            client = httpx.Client(
                follow_redirects=True,
                transport=_novu_transport(),
                event_hooks={"response": [mirror.on_response]},
            )
            _http_clients[id(mirror)] = client
    return Novu(api_key, client=client)


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
    def __init__(self, api_key, mirror_url=None, mirror_api_key=None):
        self.api = _novu_client(api_key, mirror_url, mirror_api_key)

    def send_notification(
        self,
        name,
        users: list[UUID],
        context: dict[str, typing.Any],
        overrides: typing.Optional[dict] = None,
        **kwargs,
    ):
        self.api.trigger(
            trigger_event_request_dto=TriggerEventRequestDto(
                workflow_id=name,  # This is the slug of the workflow name.
                to=[str(u) for u in users],
                payload=context,
                overrides=overrides if overrides else None,
            )
        )

    def send_notifications(self, dto: BulkTriggerEventDto):
        self.api.trigger_bulk(bulk_trigger_event_dto=dto)

    def get_notifications(self, page: int) -> NotificationResponse:
        """Read a page of the Novu activity feed, tolerant to non-channel jobs.

        Novu's activity feed includes jobs without a ``providerId`` (in-app /
        digest / trigger steps), but the SDK's ``ActivityNotificationJobResponseDto``
        marks ``providerId`` as required, so ``notifications.list()`` raises
        ``ResponseValidationError`` for the *whole page* when any such job is
        present — which silently blocked all Novu-sourced "sent" event tracking.

        We parse the response leniently from the raw JSON instead of relying on
        the strict typed models, so one provider-less job no longer drops the
        entire page.
        """
        try:
            response = self.api.notifications.list(request={"page": page})
            result = response.result
            raw = {
                "page": result.page,
                "hasMore": result.has_more,
                "pageSize": result.page_size,
                # by_alias keeps the camelCase keys, so this matches the raw-body
                # shape used in the fallback below (one parsing path for both).
                "data": [dto.model_dump(by_alias=True) for dto in result.data],
            }
        except ResponseValidationError as error:
            logger.warning(
                "Novu notifications.list failed strict validation (page %s); "
                "parsing raw response leniently: %s",
                page,
                error,
            )
            raw = error.raw_response.json()

        return self._build_notification_response(raw)

    @staticmethod
    def _build_notification_response(raw: dict) -> NotificationResponse:
        data = [Notifier._build_notification(item) for item in raw.get("data", [])]
        return NotificationResponse(
            page=raw.get("page", 0),
            has_more=raw.get("hasMore", False),
            page_size=raw.get("pageSize", 0),
            _data=data,
        )

    @staticmethod
    def _build_notification(item: dict) -> "ActivityNotificationDto":
        subscriber = item.get("subscriber") or {}
        template = item.get("template") or {}
        triggers = [
            ActivityNotificationTriggerResponseDto(
                type=trigger.get("type", ""),
                identifier=trigger.get("identifier", ""),
                variables=trigger.get("variables", []),
            )
            for trigger in (template.get("triggers") or [])
        ]
        return ActivityNotificationDto(
            _environment_id=item.get("_environmentId", ""),
            _organization_id=item.get("_organizationId", ""),
            transaction_id=item.get("transactionId", ""),
            created_at=item.get("createdAt", ""),
            channels=item.get("channels") or [],
            subscriber=ActivityNotificationSubscriberResponseDTO(
                _id=subscriber.get("_id", ""),
                email=subscriber.get("email", ""),
                first_name=subscriber.get("firstName", ""),
                last_name=subscriber.get("lastName", ""),
                phone=subscriber.get("phone", ""),
            ),
            template=ActivityNotificationTemplateResponseDto(
                name=template.get("name", ""),
                triggers=triggers,
                _id=template.get("_id", ""),
            ),
            # Keep jobs as raw dicts: the consumer accesses jobs[0]["status"] /
            # jobs[0]["payload"], and this avoids re-validating providerId.
            jobs=item.get("jobs") or [],
            _subscriber=item.get("_subscriberId", ""),
        )


class SubscriberManager:
    def __init__(self, api_key, mirror_url=None, mirror_api_key=None):
        self.api = _novu_client(api_key, mirror_url, mirror_api_key)

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
