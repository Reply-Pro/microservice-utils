import os
import signal
import threading
import time
from unittest import mock

import pytest

from microservice_utils.google_cloud.adapters.pubsub import Publisher, Subscriber

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


PUBLISHER_PATCH_TARGET = (
    "microservice_utils.google_cloud.adapters.pubsub.pubsub.PublisherClient"
)


def _in_threads(count, target):
    """Runs ``target`` in ``count`` threads that start it together; what each
    returned, and what any raised."""
    start = threading.Barrier(count)
    returned, raised = [], []

    def run():
        try:
            start.wait(timeout=5)
            returned.append(target())
        except Exception as error:
            raised.append(error)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return returned, raised


def test_threads_publishing_first_at_once_share_one_client():
    """The client is built on the first publish. Threads that all find it
    missing used to build one each and drop all but one."""
    built = []

    def slow_client(*args, **kwargs):
        time.sleep(0.05)
        built.append(object())
        return built[-1]

    publisher = Publisher("test-project")
    with mock.patch(PUBLISHER_PATCH_TARGET, side_effect=slow_client):
        returned, raised = _in_threads(5, publisher._get_client)

    assert raised == []
    assert len(built) == 1
    assert returned == built * 5


def test_publishers_build_their_clients_at_the_same_time():
    """A build can take seconds when credentials come from the metadata
    server: one Publisher's build must not wait for another's."""
    both_building = threading.Barrier(2)

    def client_built_beside_another(*args, **kwargs):
        both_building.wait(timeout=2)
        return object()

    publishers = iter([Publisher("test-project"), Publisher("test-project")])
    with mock.patch(PUBLISHER_PATCH_TARGET, side_effect=client_built_beside_another):
        returned, raised = _in_threads(2, lambda: next(publishers)._get_client())

    assert raised == []
    assert len(returned) == 2


def test_a_subclass_that_skips_init_still_builds_its_client():
    class Fake(Publisher):
        def __init__(self):
            self._args, self._kwargs = (), {}

    with mock.patch(PUBLISHER_PATCH_TARGET) as client_cls:
        assert Fake()._get_client() is client_cls.return_value


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_a_child_forked_while_its_client_is_built_builds_its_own():
    """The child gets the lock as held by a thread it doesn't have: it must
    not wait for it."""
    building, finish = threading.Event(), threading.Event()

    def held_client(*args, **kwargs):
        building.set()
        finish.wait(timeout=5)
        return object()

    publisher = Publisher("test-project")
    with mock.patch(PUBLISHER_PATCH_TARGET, side_effect=held_client):
        builder = threading.Thread(target=publisher._get_client)
        builder.start()
        assert building.wait(timeout=5)
        child = os.fork()
        if child == 0:
            # Killed by the alarm if it waits for the lock.
            signal.alarm(3)
            finish.set()
            publisher._get_client()
            os._exit(0)
        finish.set()
        builder.join(timeout=5)
        _, status = os.waitpid(child, 0)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
