# Known issues: Novu

Open problems in `microservice_utils/novu`. The PR that fixes one removes its entry.

## The mirror loses copies still queued when the process exits or is paused

**Where:** `microservice_utils/novu/mirror.py`, `NovuMirror`. Copies are sent by a daemon thread
from an in-memory queue, and nothing sends what is left in the queue when the process exits.
`NovuMirror.close()` does, but only a caller that knows about the mirror calls it.

**What happens:**

- **A short-lived process** (a management command, a one-off script) that triggers a
  notification and exits at once loses that copy. Novu sends the notification; the endpoint
  never hears of it.
- **A pod that restarts** (deploy, scale-down, eviction) loses whatever is queued at that moment.
  Normally very little, since a copy leaves within milliseconds when the endpoint is up.
- **Cloud Run with CPU throttling** (the default; integrations-service runs this way) gives the
  thread CPU only while a request is in flight. A copy queued at the end of a request waits for
  the next request, and is lost if the instance shuts down first.

**Impact:** the copy endpoint misses notifications Novu did send. Nothing reaches the caller or
changes what Novu sends. For Reply Pro's notification engine this leaves gaps in the comparison
with Novu's activity. The mirror is documented as best effort (`docs/novu/mirror.md`), so this
is the cost of that choice, not a contract break.

**Verified:** yes, the first case, 2026-11-17 in production. A password reset requested from a
`manage.py shell` that exited straight away reached Novu but not the engine. The same request
with `mirror_from_settings().close(timeout=20)` before exiting arrived (`dropped == 0`). The pod
and Cloud Run cases follow from the same mechanism and were not observed.

**How to verify:** in a shell with `NOVU_MIRROR_URL`/`NOVU_MIRROR_API_KEY` set, trigger one
notification and exit without calling `close()`. The endpoint's access log shows no
`POST /v1/events/trigger`. With `close()` it does.

**Possible fix:** register `close(timeout=a few seconds)` with `atexit` (and on SIGTERM, which
the services already handle for Pub/Sub) so a normal exit sends what is queued. For Cloud Run,
send the copy inside the request instead of the thread (an option on the mirror), at the cost of
the endpoint's latency on every Novu call.
