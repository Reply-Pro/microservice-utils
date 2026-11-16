"""Stand-ins for Novu and for the mirror's endpoint, shared by the Novu tests."""

import json
import threading

import httpx


class FakeNovu:
    """Answers like Novu, and keeps what it was asked."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status = 201
        self.transaction_ids = iter(f"novu-tx-{n}" for n in range(1, 100))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(
                self.status, json={"statusCode": self.status, "message": "refused"}
            )
        path = request.url.path
        if path == "/v1/events/trigger":
            body = json.loads(request.content)
            tx = body.get("transactionId") or next(self.transaction_ids)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "acknowledged": True,
                        "status": "processed",
                        "transactionId": tx,
                    }
                },
            )
        if path == "/v1/events/trigger/bulk":
            events = json.loads(request.content)["events"]
            return httpx.Response(
                201,
                json={
                    "data": [
                        {
                            "acknowledged": True,
                            "status": "processed",
                            "transactionId": e.get("transactionId")
                            or next(self.transaction_ids),
                        }
                        for e in events
                    ]
                },
            )
        if path.startswith("/v2/subscribers") and request.method == "POST":
            body = json.loads(request.content)
            subscriber = {
                "_id": "x",
                "subscriberId": body["subscriberId"],
                "_organizationId": "org",
                "_environmentId": "env",
                "deleted": False,
                "createdAt": "2026-11-16T12:00:00.000Z",
                "updatedAt": "2026-11-16T12:00:00.000Z",
            }
            return httpx.Response(201, json={"data": subscriber})
        if path.startswith("/v2/subscribers/") and request.method == "PATCH":
            return httpx.Response(
                200, json={"data": {"subscriberId": path.split("/")[-1]}}
            )
        if path.startswith("/v1/events/trigger/") and request.method == "DELETE":
            return httpx.Response(200, json={"data": True})
        if path.startswith("/v2/subscribers") and request.method == "DELETE":
            return httpx.Response(
                200, json={"data": {"acknowledged": True, "status": "deleted"}}
            )
        if path == "/v1/notifications":
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "page": 0,
                    "hasMore": False,
                    "pageSize": 10,
                    "totalCount": 0,
                },
            )
        return httpx.Response(404, json={"message": "not here"})


class FakeEngine:
    """The mirror's destination. Can be told to be slow or down."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.hold: threading.Event | None = None
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.hold is not None:
            self.hold.wait(timeout=10)
        if self.down:
            raise httpx.ConnectError("engine unreachable")
        self.requests.append(request)
        return httpx.Response(201, json={"data": {"acknowledged": True}})

    def bodies(self):
        return [json.loads(r.content) if r.content else None for r in self.requests]
