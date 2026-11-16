# Novu adapters and the mirror

`microservice_utils/novu/adapters.py` wraps the Novu SDK (`novu-py`, pinned in the `novu` extra)
for our services: `Notifier` sends notifications and reads the activity feed, and
`SubscriberManager` creates and deletes subscribers. Both take the Novu API key; callers pass it,
the library reads no Novu key from the environment.

## The mirror (`microservice_utils/novu/mirror.py`)

The mirror copies every write Novu accepted to a second, Novu-compatible endpoint. Reply Pro uses
it to shadow Novu with its own notification engine, which processes the same notifications without
sending them.

**Switching it on.** Set both environment variables in the service:

- `NOVU_MIRROR_URL`: the endpoint's base URL, for example `https://notifications.example.com`.
- `NOVU_MIRROR_API_KEY`: the key the endpoint expects in `Authorization: ApiKey <key>`.

`Notifier(api_key, mirror_url=..., mirror_api_key=...)` and the same arguments on
`SubscriberManager` replace the environment: when either argument is given, both come from the
arguments, so a URL set in code is never paired with the key the environment holds. With either
value missing there is no mirror, and the Novu client is built exactly as before (`Novu(api_key)`).

**What is copied.** A request is copied once Novu answered it with a 2xx:

| Request | Copied |
| --- | --- |
| `POST /v1/events/trigger`, `POST /v1/events/trigger/bulk` | yes |
| `DELETE /v1/events/trigger/{transactionId}` (cancel) | yes |
| `POST /v2/subscribers`, `PATCH` and `DELETE /v2/subscribers/{id}` | yes |
| everything else, the activity feed (`GET /v1/notifications`) above all | no |

A trigger sent without a `transactionId` is copied with the one Novu returned (each event of a bulk
trigger with its own), so the same notification has the same id on both sides. The copy carries
the mirror's key, never Novu's.

**It never gets in the way:**

- The copy is made after Novu answered, from the answer the SDK is about to read, which it leaves
  as it is: the caller gets exactly what Novu returned, errors included.
- It is an httpx response hook on the client the SDK sends through, so every path through the SDK
  is covered, including subclasses that call `self.api.trigger` directly. Being a hook, it leaves
  httpx's own transport and proxy selection (`HTTPS_PROXY` and the like) as it was.
- Logs name the operation (`trigger`, `subscriber delete`...) and the status or error type only,
  never the path, which can hold an email address, nor the mirror's answer.
- Copies wait in a bounded queue (1000) and are sent by one background thread per process (a
  forked worker starts its own), with a 5 second timeout and up to 3 attempts on a connection error
  or a 5xx. A 4xx from the mirror is logged and not retried.
- When the queue is full a new copy is dropped and counted (`NovuMirror.dropped`), with a warning
  on the first and every hundredth.
- Nothing in the mirror raises into the caller. A copy still waiting when the process exits is
  lost: the mirror is best effort.
- The SDK retries a failed call for up to an hour, each attempt with a new idempotency key. Only an
  attempt Novu accepted is copied, so a call is copied once.

**One HTTP client per process.** With the mirror on, every Novu client of the process shares one
`httpx.Client`: the SDK never closes a client it is given, and services build a new `Notifier` per
injection.
