from microservice_utils.adapters.google_cloud.cloud_tasks import InMemoryEnqueuer


def test_in_memory_enqueuer():
    enqueuer = InMemoryEnqueuer()

    enqueuer.enqueue_http_request("http://fake.fake", "fake_tasks", b'{"test": 1}')

    assert len(enqueuer.enqueued_tasks) == 1
