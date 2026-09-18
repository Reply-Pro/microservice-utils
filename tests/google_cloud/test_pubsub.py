from unittest import mock

from microservice_utils.google_cloud.adapters.pubsub import Subscriber

PATCH_TARGET = "microservice_utils.google_cloud.adapters.pubsub.pubsub.SubscriberClient"


def _subscribe_kwargs(client_cls):
    return client_cls.return_value.subscribe.call_args.kwargs


@mock.patch(PATCH_TARGET)
def test_subscribe_forwards_await_callbacks_on_shutdown(client_cls):
    subscriber = Subscriber("test-project", prepend_value="staging")

    subscriber.subscribe(
        "accounts.users", lambda message: None, await_callbacks_on_shutdown=True
    )

    assert _subscribe_kwargs(client_cls)["await_callbacks_on_shutdown"] is True


@mock.patch(PATCH_TARGET)
def test_subscribe_defaults_to_the_pubsub_default(client_cls):
    subscriber = Subscriber("test-project")

    subscriber.subscribe("accounts.users", lambda message: None)

    assert _subscribe_kwargs(client_cls)["await_callbacks_on_shutdown"] is False


@mock.patch(PATCH_TARGET)
def test_subscribe_forwards_unrecognized_kwargs(client_cls):
    """They used to be accepted and silently dropped, which quietly ignored any
    option the caller thought it was setting."""

    subscriber = Subscriber("test-project")

    subscriber.subscribe(
        "accounts.users", lambda message: None, use_legacy_flow_control=True
    )

    assert _subscribe_kwargs(client_cls)["use_legacy_flow_control"] is True


@mock.patch(PATCH_TARGET)
def test_shutdown_cancels_and_drains_every_subscription(client_cls):
    subscriber = Subscriber("test-project")
    subscriber.subscribe("accounts.users", lambda message: None)
    subscriber.subscribe("accounts.billing", lambda message: None)

    subscriber.shutdown()

    future = client_cls.return_value.subscribe.return_value
    assert future.cancel.call_count == 2
    assert future.result.call_count == 2
