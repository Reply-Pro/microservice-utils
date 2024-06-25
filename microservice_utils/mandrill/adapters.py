import typing
from dataclasses import dataclass
import logging

import mailchimp_transactional as MailchimpTransactional
from mailchimp_transactional.api_client import ApiClientError

logger = logging.getLogger(__name__)


@dataclass
class SmtpEvent:
    ts: int
    type: str
    diag: str
    source_ip: str
    destination_ip: str
    size: int


@dataclass
class OpenDetail:
    ts: int
    ip: str
    location: str
    ua: typing.Optional[str] = None


@dataclass
class Notification:
    email: str
    subject: str
    _id: str
    state: str
    sender: str
    smtp_events: typing.List[SmtpEvent]
    opens: int
    clicks: int
    opens_detail: typing.List[OpenDetail]
    clicks_detail: typing.List[dict]


@dataclass
class ContentResponse:
    from_email: str
    from_name: str
    subject: str
    html: str
    text: str
    to: dict
    headers: dict
    attachments: typing.List[dict]
    ts: int
    tags: typing.List[str]
    _id: str


class Notifier:
    def __init__(self, api_key):
        self.client = MailchimpTransactional.Client(api_key)

    def search_notifications(
        self, query: str, limit: int = 50
    ) -> typing.List[Notification]:
        try:
            response = self.client.messages.search({"query": query, "limit": limit})
            notifications = [
                Notification(
                    email=item["email"],
                    subject=item["subject"],
                    _id=item["_id"],
                    state=item["state"],
                    sender=item["sender"],
                    smtp_events=[SmtpEvent(**event) for event in item["smtp_events"]],
                    opens=item["opens"],
                    clicks=item["clicks"],
                    opens_detail=[
                        OpenDetail(**detail) for detail in item["opens_detail"]
                    ],
                    clicks_detail=item["clicks_detail"],
                )
                for item in response
            ]
            return notifications
        except ApiClientError as error:
            logger.error(f"Mandrill search notifications error: {error.text}")
            return []

    def get_notification_info(self, message_id: str) -> typing.Optional[Notification]:
        try:
            response = self.client.messages.info({"id": message_id})
            notification = Notification(
                email=response["email"],
                subject=response["subject"],
                _id=response["_id"],
                state=response["state"],
                sender=response["sender"],
                smtp_events=[SmtpEvent(**event) for event in response["smtp_events"]],
                opens=response["opens"],
                clicks=response["clicks"],
                opens_detail=[
                    OpenDetail(**detail) for detail in response["opens_detail"]
                ],
                clicks_detail=response["clicks_detail"],
            )
            return notification
        except ApiClientError as error:
            logger.error(f"Mandrill get notification info error: {error.text}")
            return None

    def get_notification_content(
        self, message_id: str
    ) -> typing.Optional[ContentResponse]:
        try:
            response = self.client.messages.content({"id": message_id})
            content = ContentResponse(
                from_email=response["from_email"],
                from_name=response["from_name"],
                subject=response["subject"],
                html=response["html"],
                text=response["text"],
                to=response["to"],
                headers=response["headers"],
                attachments=response["attachments"],
                ts=response["ts"],
                tags=response["tags"],
                _id=response["_id"],
            )
            return content
        except ApiClientError as error:
            logger.error(f"Mandrill get notification content error: {error.text}")
            return None
