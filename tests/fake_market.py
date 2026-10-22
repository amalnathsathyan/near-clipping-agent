"""In-process stand-in for the NEAR Agent Market REST API, for end-to-end worker tests.

It implements only what the worker calls, with the field names from the public docs.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = "aat_" + "0" * 32


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeMarket:
    def __init__(self):
        self.assignments: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.submissions: dict[str, list[dict]] = {}
        self.started: set[str] = set()
        self.agent_id = str(uuid.uuid4())
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # -------------------------------------------------- test helpers

    def add_attachment(self, path: Path) -> dict:
        fid = str(uuid.uuid4())
        self.blobs[fid] = path.read_bytes()
        import hashlib
        return {"id": fid, "filename": path.name, "contentType": "video/mp4", "byteSize": len(self.blobs[fid]),
                "sha256": hashlib.sha256(self.blobs[fid]).hexdigest(), "createdAt": now(),
                "downloadUrl": f"/v1/files/{fid}/raw"}

    def hire(self, title: str, description: str, first_message: str | None = None,
             attachments: list[dict] | None = None) -> str:
        aid = str(uuid.uuid4())
        self.assignments[aid] = {
            "assignment": {"assignmentId": aid, "jobId": str(uuid.uuid4()), "status": "in_progress",
                           "escrowAmount": "5.0", "escrowToken": "USD", "slaSeconds": 7200,
                           "slaDeadlineAt": now(), "startedAt": None, "submittedAt": None,
                           "deliverableUrl": None, "bidId": None},
            "job": {"jobId": "", "title": title, "description": description, "tags": ["video"],
                    "status": "in_progress"},
            "latestMessage": None,
        }
        self.messages[aid] = []
        if first_message or attachments:
            self.buyer_says(aid, first_message or "", attachments or [])
        return aid

    def buyer_says(self, aid: str, body: str, attachments: list[dict] | None = None) -> None:
        m = {"messageId": str(uuid.uuid4()), "assignmentId": aid, "senderAccountId": "buyer", "origin": "buyer",
             "body": body, "createdAt": now(), "attachments": attachments or []}
        with self.lock:
            self.messages[aid].append(m)
            self.assignments[aid]["latestMessage"] = {**m, "truncated": False}

    def request_changes(self, aid: str, body: str) -> None:
        self.buyer_says(aid, body)
        self.assignments[aid]["assignment"]["status"] = "in_progress"

    # -------------------------------------------------- HTTP

    def _handler(self):
        market = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body=None, raw: bytes | None = None, ctype="application/json"):
                data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"error": "unauthorized"})
                    return False
                return True

            def _json(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if not self._auth():
                    return
                path = self.path.split("?")[0]
                if path == "/v1/agents/me/assignments":
                    rows = [r for r in market.assignments.values() if r["assignment"]["status"] == "in_progress"]
                    return self._send(200, {"assignments": rows})
                m = re.fullmatch(r"/v1/assignments/([\w-]+)/messages", path)
                if m:
                    return self._send(200, {"messages": list(reversed(market.messages[m.group(1)]))})
                m = re.fullmatch(r"/v1/files/([\w-]+)/raw", path)
                if m and m.group(1) in market.blobs:
                    return self._send(200, raw=market.blobs[m.group(1)], ctype="application/octet-stream")
                return self._send(404, {"error": "not found"})

            def do_PUT(self):
                m = re.fullmatch(r"/upload/([\w-]+)", self.path)
                if not m:
                    return self._send(404)
                n = int(self.headers.get("Content-Length") or 0)
                market.blobs[m.group(1)] = self.rfile.read(n)
                return self._send(200, {})

            def do_POST(self):
                if not self._auth():
                    return
                path = self.path.split("?")[0]
                m = re.fullmatch(r"/v1/assignments/([\w-]+)/start", path)
                if m:
                    a = market.assignments[m.group(1)]["assignment"]
                    a["startedAt"] = a["startedAt"] or now()
                    market.started.add(m.group(1))
                    return self._send(200, {"assignmentId": m.group(1), "startedAt": a["startedAt"]})
                m = re.fullmatch(r"/v1/assignments/([\w-]+)/messages", path)
                if m:
                    body = self._json().get("body", "")
                    if not 1 <= len(body) <= 4000:
                        return self._send(400, {"error": "body length"})
                    msg = {"messageId": str(uuid.uuid4()), "assignmentId": m.group(1), "senderAccountId": "owner",
                           "senderAgentId": market.agent_id, "origin": "agent", "body": body, "createdAt": now()}
                    with market.lock:
                        market.messages[m.group(1)].append(msg)
                        market.assignments[m.group(1)]["latestMessage"] = {**msg, "truncated": False}
                    return self._send(201, msg)
                if path == "/v1/files":
                    req = self._json()
                    if not req.get("filename") or not req.get("byteSize"):
                        return self._send(422, {"error": "filename and byteSize required"})
                    fid = str(uuid.uuid4())
                    market.files[fid] = {"req": req, "complete": False}
                    return self._send(201, {"fileId": fid, "uploadUrl": f"/upload/{fid}",
                                            "directUploadUrl": f"{market.base}/upload/{fid}"})
                m = re.fullmatch(r"/v1/files/([\w-]+)/complete", path)
                if m:
                    fid = m.group(1)
                    if fid not in market.blobs:
                        return self._send(409, {"error": "no bytes"})
                    market.files[fid]["complete"] = True
                    return self._send(200, {"fileId": fid, "fileUrl": f"{market.base}/v1/files/{fid}/raw"})
                m = re.fullmatch(r"/v1/assignments/([\w-]+)/submit", path)
                if m:
                    aid = m.group(1)
                    req = self._json()
                    if not req.get("deliverableMarkdown"):
                        return self._send(400, {"error": "deliverableMarkdown required"})
                    files = req.get("files") or []
                    if any(not isinstance(f, str) for f in files):
                        return self._send(422, {"error": "files must be URLs"})
                    market.submissions.setdefault(aid, []).append(req)
                    a = market.assignments[aid]["assignment"]
                    a["submittedAt"] = now()
                    a["status"] = "submitted"
                    a["deliverableUrl"] = f"{market.base}/deliverables/{aid}"
                    return self._send(200, a)
                return self._send(404, {"error": "not found"})

        return H
