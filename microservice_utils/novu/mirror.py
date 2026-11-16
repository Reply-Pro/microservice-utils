"""Copy what Novu accepted to a second, Novu-compatible endpoint.

Used to shadow Novu with Reply Pro's notification engine: every write our services make
to Novu (a trigger, a bulk trigger, a cancel, a subscriber created, updated or deleted)
is sent again to the mirror, once Novu has accepted it. Reads, such as the activity
feed, are not.

The copy never changes what the caller gets from Novu and never makes it wait:

- it happens after Novu answered, from the answer the SDK is about to read, which it
  leaves untouched;
- it is queued and sent on a background thread, with a short timeout and a few retries;
- when too much is waiting, new copies are dropped (and counted), never waited for;
- nothing it does raises into the caller.

A trigger Novu gave a transaction id to is copied with that id, so the same notification
carries the same id on both sides.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

TRIGGER = "/v1/events/trigger"
BULK_TRIGGER = "/v1/events/trigger/bulk"
SUBSCRIBERS = "/v2/subscribers"


def operation(method: str, path: str) -> str:
    """A name for the call that says nothing about whom it is for, for the logs."""
    if path == TRIGGER:
        return "trigger"
    if path == BULK_TRIGGER:
        return "bulk trigger"
    if path.startswith(TRIGGER + "/"):
        return "trigger cancel"
    if path == SUBSCRIBERS or path.startswith(SUBSCRIBERS + "/"):
        verb = {"POST": "create", "PATCH": "update", "DELETE": "delete"}.get(
            method, method
        )
        return f"subscriber {verb}"
    return method


def is_mirrored(method: str, path: str) -> bool:
    """The writes worth copying; everything else (reads above all) stays Novu's."""
    if method == "POST":
        return path in (TRIGGER, BULK_TRIGGER, SUBSCRIBERS)
    if method in ("PATCH", "DELETE") and path.startswith(SUBSCRIBERS + "/"):
        return True
    return (
        method == "DELETE" and path.startswith(TRIGGER + "/") and path != BULK_TRIGGER
    )


def _answer_data(response: httpx.Response):
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("data", body) if isinstance(body, dict) else body


def with_transaction_ids(path: str, content: bytes, response: httpx.Response) -> bytes:
    """The request as sent to Novu, plus the transaction ids Novu gave the events
    that had none."""
    if path not in (TRIGGER, BULK_TRIGGER) or not content:
        return content
    try:
        body = json.loads(content)
    except ValueError:
        return content
    data = _answer_data(response)
    if path == TRIGGER and isinstance(body, dict) and isinstance(data, dict):
        events, answers = [body], [data]
    elif path == BULK_TRIGGER and isinstance(body, dict) and isinstance(data, list):
        events, answers = body.get("events") or [], data
    else:
        return content
    for event, answer in zip(events, answers, strict=False):
        given = isinstance(answer, dict) and answer.get("transactionId")
        if isinstance(event, dict) and not event.get("transactionId") and given:
            event["transactionId"] = given
    return json.dumps(body).encode()


@dataclass(frozen=True)
class Copy:
    method: str
    path: str  # with its query string; may hold an id or an address: never logged
    content: bytes
    operation: str


_STOP = object()


class NovuMirror:
    """Sends copies to the mirror. One per process: share it between Novu clients."""

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        background: bool = True,
        transport: httpx.BaseTransport | None = None,
        queue_size: int = 1000,
        timeout: float = 5.0,
        attempts: int = 3,
    ):
        self.url = url.rstrip("/")
        self._api_key = api_key
        #: False sends in the caller's thread (tests); a service never does.
        self._background = background
        self._client = httpx.Client(
            base_url=self.url, timeout=timeout, transport=transport
        )
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._attempts = max(1, attempts)
        self._thread: threading.Thread | None = None
        self._thread_pid: int | None = None
        self._lock = threading.Lock()
        #: Copies dropped because too many were waiting.
        self.dropped = 0

    # --- what the client hands over --------------------------------------------

    def on_response(self, response: httpx.Response) -> None:
        """An httpx response hook: copy the request if Novu accepted it.

        A hook leaves the client's own transport and proxy selection as they are.
        The mirror itself never raises; reading Novu's answer can, and then the
        caller gets that error exactly as it would without the mirror.
        """
        request = response.request
        if not is_mirrored(request.method, request.url.path) or not (
            200 <= response.status_code < 300
        ):
            return
        # Small JSON answers; reading them here leaves them cached for the SDK. A
        # failed read is Novu's error, not the mirror's: it goes on to the caller
        # (the SDK retries a timeout), and a body read once cannot be read again.
        response.read()
        self.copy(request, response)

    def copy(self, request: httpx.Request, response: httpx.Response) -> None:
        """Copy ``request`` if Novu accepted it. Never raises."""
        try:
            path = request.url.path
            if (
                not is_mirrored(request.method, path)
                or not 200 <= response.status_code < 300
            ):
                return
            content = with_transaction_ids(path, request.content, response)
            query = (
                request.url.query.decode()
                if isinstance(request.url.query, bytes)
                else request.url.query
            )
            item = Copy(
                request.method,
                path + (f"?{query}" if query else ""),
                content,
                operation(request.method, path),
            )
            if self._background:
                self._enqueue(item)
            else:
                self._send(item)
        except Exception as error:
            logger.warning(
                "Novu mirror: could not copy a request: %s", type(error).__name__
            )

    def close(self, timeout: float = 5.0) -> None:
        """Send what is waiting (up to ``timeout``) and stop the thread."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put(_STOP, timeout=timeout)
            except queue.Full:
                pass
            thread.join(timeout=timeout)
        self._client.close()

    # --- inside -------------------------------------------------------------------

    def _enqueue(self, item: Copy) -> None:
        self._ensure_thread()
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 100 == 0:
                logger.warning(
                    "Novu mirror: %s copies dropped, the mirror is not keeping up",
                    self.dropped,
                )

    def _ensure_thread(self) -> None:
        # A forked worker inherits the parent's object but not its thread: one per
        # process.
        if (
            self._thread is not None
            and self._thread_pid == os.getpid()
            and self._thread.is_alive()
        ):
            return
        with self._lock:
            if (
                self._thread is not None
                and self._thread_pid == os.getpid()
                and self._thread.is_alive()
            ):
                return
            self._thread = threading.Thread(
                target=self._run, name="novu-mirror", daemon=True
            )
            self._thread_pid = os.getpid()
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self._send(item, retry=True)
            except Exception as error:
                logger.warning(
                    "Novu mirror: sending a copy failed: %s", type(error).__name__
                )

    def _send(self, item: Copy, retry: bool = False) -> None:
        attempts = self._attempts if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(
                    item.method,
                    item.path,
                    content=item.content or None,
                    headers={
                        "Authorization": f"ApiKey {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.HTTPError as error:
                problem = f"unreachable ({type(error).__name__})"
            else:
                if response.status_code < 500:
                    if response.status_code >= 400:
                        # Not retried: the mirror refused it (unknown workflow, bad
                        # request). Neither the path nor the answer is logged: either
                        # can name a person or carry a payload.
                        logger.warning(
                            "Novu mirror refused a %s: %s",
                            item.operation,
                            response.status_code,
                        )
                    return
                problem = f"answered {response.status_code}"
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
            else:
                logger.warning(
                    "Novu mirror gave up on a %s: %s", item.operation, problem
                )
