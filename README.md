# microservice-utils

Utilities and adapters for speeding up microservice development.

## GCP Pub/Sub
You can subscribe to multiple subscriptions by subsequently calling `subscribe()`. `wait_for_shutdown` will block IO
for all the subscriptions and wait for the app to be signaled to shut down.

```python
from microservice_utils.google_cloud.adapters.pubsub import Subscriber

subscriber = Subscriber("your-gcp-project-id", prepend_value="staging")

with subscriber:
    subscriber.subscribe(
        "accounts__users", sample_handler
    )

    try:
        subscriber.wait_for_shutdown()
    except KeyboardInterrupt:
        # Gracefully shut down in response to Ctrl+C (or other events)
        subscriber.shutdown()
```

## Releasing a new version
- Update the package version using semver rules (`microservice-utils/__init__.py`)
- Commit and push change
- Create a new tag with the version (e.g. `git tag -a vx.x.x -m ''`)
- `git push --tags` to push the new tag and start the release workflow

## Todos

- [x] Events
- [x] GCP Pub/Sub
- [x] GCP Cloud Tasks
- [ ] JWT validation utils
- [x] Logging
