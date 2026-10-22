"""NEAR Agent Market REST client (self-hosted agent, `aat_` token).

Endpoints follow docs.market.near.ai (Self-hosted agents) and /skill/SKILL.md. The public docs don't
spell out every field of the file upload and submit bodies, so those calls accept a few response
shapes and remember which submit shape the server took. `clipper probe` exercises them for real.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


class MarketError(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


class MarketClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None):
        if not token:
            raise MarketError("MARKET_AGENT_TOKEN is not set")
        self.base = base_url.rstrip("/")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self._submit_shape: str | None = None

    # ------------------------------------------------------------ plumbing

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}{path if path.startswith('/') else '/' + path}"

    def request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
                retries: int = 4, timeout: float = 60) -> Any:
        for attempt in range(retries + 1):
            try:
                r = self.s.request(method, self._url(path), json=json_body, params=params, timeout=timeout)
            except requests.RequestException as e:
                if attempt == retries:
                    raise MarketError(f"{method} {path}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    raise MarketError(f"{method} {path} answered {r.status_code}", r.status_code, r.text[:500])
                time.sleep(float(r.headers.get("retry-after") or 2 ** (attempt + 1)))
                continue
            if not r.ok:
                raise MarketError(f"{method} {path} answered {r.status_code}: {r.text[:300]}", r.status_code, r.text)
            if not r.content:
                return None
            try:
                return r.json()
            except ValueError:
                return r.text
        return None

    # ------------------------------------------------------------ assignments

    def assignments(self, status: str = "in_progress") -> list[dict]:
        rows, offset = [], 0
        while True:
            page = self.request("GET", "/v1/agents/me/assignments",
                                params={"status": status, "limit": 100, "offset": offset}) or {}
            batch = page.get("assignments", []) if isinstance(page, dict) else []
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            offset += 100

    def start(self, assignment_id: str) -> Any:
        return self.request("POST", f"/v1/assignments/{assignment_id}/start")

    def send_message(self, assignment_id: str, body: str) -> None:
        body = body.strip()
        # The API takes 1-4000 characters per message; split longer text on paragraph breaks.
        chunks, cur = [], ""
        for para in body.split("\n\n"):
            if len(cur) + len(para) + 2 > 3900 and cur:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n\n{para}" if cur else para
        if cur:
            chunks.append(cur)
        for c in chunks:
            self.request("POST", f"/v1/assignments/{assignment_id}/messages", json_body={"body": c[:4000]})

    def messages(self, assignment_id: str, limit: int = 100) -> list[dict]:
        data = self.request("GET", f"/v1/assignments/{assignment_id}/messages", params={"limit": limit}) or {}
        if isinstance(data, list):
            return data
        return data.get("messages") or data.get("items") or data.get("data") or []

    def bid_messages(self, bid_id: str) -> list[dict]:
        try:
            data = self.request("GET", f"/v1/bids/{bid_id}/messages", params={"limit": 100}) or {}
        except MarketError as e:
            log.info("bid thread unavailable: %s", e)
            return []
        if isinstance(data, list):
            return data
        return data.get("messages") or data.get("items") or []

    def job(self, job_id: str) -> dict:
        try:
            return self.request("GET", f"/v1/jobs/{job_id}") or {}
        except MarketError as e:
            log.info("job read failed: %s", e)
            return {}

    # ------------------------------------------------------------ files

    def download(self, url: str, dest: Path, sha256: str | None = None) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):
            with self.s.get(self._url(url), stream=True, timeout=120) as r:
                if r.status_code == 503:  # still being virus-scanned
                    time.sleep(float(r.headers.get("retry-after") or 10))
                    continue
                if not r.ok:
                    raise MarketError(f"download {url} answered {r.status_code}", r.status_code, r.text[:300])
                h = hashlib.sha256()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
            if sha256 and h.hexdigest() != sha256.lower():
                raise MarketError(f"downloaded file failed its sha256 check: {dest.name}")
            return dest
        raise MarketError(f"file {url} was still being scanned after several tries")

    def upload(self, path: Path) -> dict:
        """Reserve, send bytes, complete. Returns {"fileId", "fileUrl"}."""
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        sha = hashlib.sha256(data).hexdigest()
        full = {"filename": path.name, "contentType": ctype, "byteSize": len(data), "sha256": sha, "link": True}
        try:
            res = self.request("POST", "/v1/files", json_body=full)
        except MarketError as e:
            if e.status not in (400, 422):
                raise
            res = self.request("POST", "/v1/files", json_body={"filename": path.name, "contentType": ctype,
                                                                 "byteSize": len(data), "link": True})
        res = res or {}
        file_obj = res.get("file") if isinstance(res.get("file"), dict) else {}
        file_id = res.get("fileId") or res.get("id") or file_obj.get("fileId") or file_obj.get("id")
        direct = res.get("directUploadUrl")
        upload_url = res.get("uploadUrl")
        if not file_id or not (direct or upload_url):
            raise MarketError(f"unexpected reserve response: {str(res)[:300]}")
        extra_headers = res.get("uploadHeaders") or res.get("headers") or {}
        put_headers = {"Content-Type": ctype, **extra_headers}
        if direct:
            r = requests.put(direct, data=data, headers=put_headers, timeout=600)
        else:
            method = (res.get("uploadMethod") or "PUT").upper()
            r = self.s.request(method, self._url(upload_url), data=data, headers=put_headers, timeout=600)
        if not r.ok:
            raise MarketError(f"byte upload answered {r.status_code}: {r.text[:300]}", r.status_code, r.text)
        done = self.request("POST", f"/v1/files/{file_id}/complete", json_body={}) or {}
        url = (done.get("fileUrl") or done.get("url") or done.get("link") or res.get("fileUrl")
               or (done.get("file") or {}).get("fileUrl") or (done.get("file") or {}).get("url"))
        return {"fileId": file_id, "fileUrl": url or f"{self.base}/v1/files/{file_id}/raw", "raw": done}

    # ------------------------------------------------------------ submit

    def submit(self, assignment_id: str, markdown: str, files: list[dict]) -> Any:
        md = markdown.encode("utf-8")[: 256 * 1024 - 16].decode("utf-8", "ignore")
        shapes = {
            "urls": lambda: [f["fileUrl"] for f in files],
            "ids": lambda: [f["fileId"] for f in files],
            "objects": lambda: [{"fileId": f["fileId"], "fileUrl": f["fileUrl"]} for f in files],
            "none": lambda: None,
        }
        order = [self._submit_shape] if self._submit_shape else []
        order += [k for k in shapes if k not in order]
        last: MarketError | None = None
        for name in order:
            body: dict[str, Any] = {"deliverableMarkdown": md}
            val = shapes[name]() if files else None
            if val is not None:
                body["files"] = val
            elif files and name != "none":
                continue
            try:
                res = self.request("POST", f"/v1/assignments/{assignment_id}/submit", json_body=body)
                self._submit_shape = name
                if name == "none" and files:
                    log.warning("submit accepted only without a files list; clips are linked in the Markdown")
                return res
            except MarketError as e:
                if e.status not in (400, 422):
                    raise
                log.info("submit shape %s rejected: %s", name, e.body[:200])
                last = e
        raise last or MarketError("submit failed")


def find_attachments(obj: Any) -> list[dict]:
    """Every attachment-like dict (has a download URL) anywhere inside a JSON value."""
    found: list[dict] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            url = x.get("downloadUrl") or x.get("download_url")
            if isinstance(url, str):
                found.append({"url": url, "filename": x.get("filename") or "attachment",
                              "sha256": x.get("sha256"), "contentType": x.get("contentType") or x.get("content_type") or "",
                              "id": x.get("id")})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    seen, out = set(), []
    for a in found:
        if a["url"] not in seen:
            seen.add(a["url"])
            out.append(a)
    return out
