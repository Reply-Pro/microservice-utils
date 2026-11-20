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
    # A deadlock fails as one, not as a short list.
    assert not any(thread.is_alive() for thread in threads)
    return returned, raised


def _in_child(check):
    """Runs ``check`` in a forked child; its exit status: 0 when it held, 1
    when it didn't, 2 when it raised, killed by the alarm when it hung. The
    child never returns to pytest."""
    child = os.fork()
    if child == 0:
        try:
            signal.alarm(3)
            os._exit(0 if check() else 1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(child, 0)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1


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


needs_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
# Python warns of a fork while other threads run; that is what these test.
forks_beside_threads = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning"
)


@needs_fork
@forks_beside_threads
def test_a_child_forked_while_its_client_is_built_builds_its_own():
    """The child gets the lock as held by a thread it doesn't have: it must
    not wait for it."""
    building, finish = threading.Event(), threading.Event()

    def held_client(*args, **kwargs):
        building.set()
        finish.wait(timeout=5)
        return object()

    def builds():
        finish.set()
        return publisher._get_client() is not None

    publisher = Publisher("test-project")
    with mock.patch(PUBLISHER_PATCH_TARGET, side_effect=held_client):
        builder = threading.Thread(target=publisher._get_client)
        builder.start()
        assert building.wait(timeout=5)
        status = _in_child(builds)
        finish.set()
        builder.join(timeout=5)

    assert status == 0


@needs_fork
def test_a_child_forked_after_the_client_is_built_builds_its_own():
    """A gRPC channel doesn't survive a fork: a worker forked from a process
    that already published must not publish over its parent's."""
    publisher = Publisher("test-project")
    with mock.patch(PUBLISHER_PATCH_TARGET, side_effect=lambda *a, **k: object()):
        parents = publisher._get_client()
        status = _in_child(lambda: publisher._get_client() is not parents)

    assert status == 0


def test_a_client_handed_in_is_kept():
    """A test or fake that sets ``_client`` itself keeps it."""
    publisher = Publisher("test-project")
    publisher._client = given = object()

    with mock.patch(PUBLISHER_PATCH_TARGET) as client_cls:
        assert publisher._get_client() is given
    client_cls.assert_not_called()
