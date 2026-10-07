"""MiniMax H3 video client: check a request against the model's limits, then
submit it, wait for it and save the MP4.

Standard library only, Python 3.9+. It speaks the asynchronous /v1/videos
protocol (submit -> poll -> content); the default endpoint is ArgoLink's.

    export MINIMAX_H3_API_KEY=...
    python3 minimax_h3.py "A paper boat drifts down a rain gutter" --duration 6
    python3 minimax_h3.py "The cat in @Image 1 naps in the sun" --image cat.png --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

DEFAULT_BASE_URL = "https://argolink.io/v1"
MODEL_ID = "minimax-h3"

# USD per billed second on 2026-10-07 (https://argolink.io/en/models/minimax-h3).
# Re-check the model page before budgeting; it wins if the numbers differ.
PRICE_PER_SECOND = {"768p": 0.039, "2k": 0.064}

MIN_SECONDS, MAX_SECONDS = 4, 15
MAX_PROMPT_CHARS = 7000
FIXED_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS, MAX_ASSETS = 9, 3, 3, 12

UPLOAD_TYPES = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
}

Asset = Union[str, Dict[str, str]]


class H3Error(Exception):
    """Raised for a request the model would reject, an HTTP error, or a job
    that ended without a video. `status` is the HTTP status when there was one;
    `payload` is the parsed error body or the final job."""

    def __init__(self, message: str, status: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _url_of(asset: Asset) -> str:
    url = asset.get("url", "") if isinstance(asset, dict) else asset
    if not isinstance(url, str) or not url.startswith("https://"):
        raise H3Error(f"asset {asset!r} must be an https:// URL (upload local files first)")
    return url


def build_request(
    prompt: str,
    *,
    duration: int = 5,
    resolution: str = "768p",
    aspect_ratio: Optional[str] = None,
    start_image: Optional[Asset] = None,
    end_image: Optional[Asset] = None,
    reference_images: Iterable[Asset] = (),
    reference_videos: Iterable[Asset] = (),
    reference_audios: Iterable[Asset] = (),
) -> Dict[str, Any]:
    """Return the JSON body for POST /videos/generations, or raise H3Error for
    anything the model page says it rejects, before any money is spent."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise H3Error("prompt is required in every mode")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise H3Error(f"prompt has {len(prompt)} characters; the limit is {MAX_PROMPT_CHARS}")
    if isinstance(duration, bool) or not isinstance(duration, int) or not MIN_SECONDS <= duration <= MAX_SECONDS:
        raise H3Error(f"duration must be a whole number of seconds from {MIN_SECONDS} to {MAX_SECONDS}")
    tier = str(resolution).lower()
    if tier not in PRICE_PER_SECOND:
        raise H3Error(f"resolution {resolution!r} is not offered; MiniMax H3 renders 768p or 2k")

    images, videos, audios = list(reference_images), list(reference_videos), list(reference_audios)
    body: Dict[str, Any] = {"model": MODEL_ID, "prompt": prompt, "duration": duration, "resolution": tier}

    if start_image is not None or end_image is not None:
        if start_image is None:
            raise H3Error("end_image needs a start_image")
        if images or videos or audios:
            raise H3Error("first/last frames cannot be combined with reference assets")
        if aspect_ratio not in (None, "adaptive"):
            raise H3Error("frame modes take their shape from the first frame: leave aspect_ratio out or send 'adaptive'")
        body["aspect_ratio"] = "adaptive"
        body["start_image"] = {"url": _url_of(start_image)}
        if end_image is not None:
            body["end_image"] = {"url": _url_of(end_image)}
        return body

    if len(images) > MAX_IMAGES or len(videos) > MAX_VIDEOS or len(audios) > MAX_AUDIOS:
        raise H3Error(f"at most {MAX_IMAGES} images, {MAX_VIDEOS} videos and {MAX_AUDIOS} audio clips")
    if len(images) + len(videos) + len(audios) > MAX_ASSETS:
        raise H3Error(f"at most {MAX_ASSETS} reference assets in total")
    if audios and not (images or videos):
        raise H3Error("reference audio needs at least one reference image or video beside it")
    has_assets = bool(images or videos or audios)
    ratio = aspect_ratio or "16:9"
    if ratio not in FIXED_RATIOS and not (ratio == "adaptive" and has_assets):
        allowed = ", ".join(FIXED_RATIOS) + (", adaptive" if has_assets else "")
        raise H3Error(f"aspect_ratio {ratio!r} is not accepted here; use one of {allowed}")
    body["aspect_ratio"] = ratio
    for field, assets in (("reference_images", images), ("reference_videos", videos), ("reference_audios", audios)):
        if assets:
            body[field] = [{"url": _url_of(a)} for a in assets]
    return body


def estimate_cost(duration: int, resolution: str = "768p", reference_video_seconds: float = 0.0) -> float:
    """Billed seconds = output seconds + reference-video seconds with the
    fraction dropped; images and audio add nothing."""
    billed = duration + math.floor(reference_video_seconds)
    return round(billed * PRICE_PER_SECOND[resolution.lower()], 4)


class MiniMaxH3:
    """Thin client for one key. Submitting is never retried here: a POST that
    timed out may still have created a billable job, so check before resending."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 timeout: float = 60.0, poll_interval: float = 10.0):
        self.api_key = api_key or os.environ.get("MINIMAX_H3_API_KEY")
        if not self.api_key:
            raise H3Error("set MINIMAX_H3_API_KEY or pass api_key")
        self.base_url = (base_url or os.environ.get("MINIMAX_H3_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _send(self, method: str, path: str, body: Optional[dict] = None, raw: bool = False) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("Accept", "video/mp4" if raw else "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = resp.read()
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")
            try:
                payload = json.loads(text)
            except ValueError:
                payload = text
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code") or error.get("type") or ""
            reason = error.get("message") or text[:200]
            raise H3Error(f"{method} {path} -> HTTP {err.code} {code}: {reason}", err.code, payload) from None
        except urllib.error.URLError as err:
            raise H3Error(f"{method} {path} failed: {err.reason}") from None
        return content if raw else json.loads(content.decode("utf-8"))

    def submit(self, body: Dict[str, Any]) -> str:
        """POST the job and return request_id, the only handle for later calls."""
        answer = self._send("POST", "/videos/generations", body)
        if not isinstance(answer, dict) or "request_id" not in answer:
            raise H3Error("submit answered without a request_id", payload=answer)
        return answer["request_id"]

    def status(self, request_id: str) -> Dict[str, Any]:
        return self._send("GET", f"/videos/{request_id}")

    def wait(self, request_id: str, max_wait: float = 1800.0,
             on_update: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Poll until done. failed and expired raise H3Error carrying the job,
        whose error.code and error.retryable say whether to resubmit."""
        give_up = time.monotonic() + max_wait
        while True:
            job = self.status(request_id)
            state = job.get("status")
            if state == "done":
                return job
            if state in ("failed", "expired"):
                error = job.get("error") or {}
                raise H3Error(f"job {request_id} {state}: {error.get('code', '')} {error.get('message', '')}".strip(),
                              payload=job)
            if on_update:
                on_update(job)
            if time.monotonic() >= give_up:
                raise H3Error(f"job {request_id} is still {state} after {max_wait:.0f}s; poll it again later",
                              payload=job)
            time.sleep(self.poll_interval)

    def download(self, request_id: str, path: str) -> str:
        """Save the finished MP4. Before done the API answers 409 job_video_not_ready."""
        partial = path + ".part"
        with open(partial, "wb") as handle:
            handle.write(self._send("GET", f"/videos/{request_id}/content", raw=True))
        os.replace(partial, path)
        return path

    def upload(self, file_path: str, kind: Optional[str] = None, content_type: Optional[str] = None) -> str:
        """Upload a local JPEG/PNG, MP4/MOV or WAV/MP3 and return its media_url
        (readable for seven days). The ticket itself is free."""
        ext = os.path.splitext(file_path)[1].lower()
        guessed_kind, guessed_type = UPLOAD_TYPES.get(ext, (None, None))
        kind = kind or guessed_kind
        content_type = content_type or guessed_type or mimetypes.guess_type(file_path)[0]
        if not kind or not content_type:
            raise H3Error(f"cannot tell the media type of {file_path}; pass kind and content_type")
        ticket = self._send("POST", "/media/uploads", {
            "model": MODEL_ID, "type": kind, "content_type": content_type,
            "size_bytes": os.path.getsize(file_path),
        })
        with open(file_path, "rb") as handle:
            put = urllib.request.Request(ticket["upload_url"], data=handle.read(), method="PUT")
        put.add_header("Content-Type", content_type)  # must equal the declared type
        try:
            urllib.request.urlopen(put, timeout=self.timeout).close()
        except urllib.error.HTTPError as err:
            raise H3Error(f"upload of {file_path} -> HTTP {err.code}", err.code) from None
        return ticket["media_url"]

    def _resolve(self, asset: Optional[Asset]) -> Optional[Asset]:
        if isinstance(asset, str) and not asset.startswith("https://") and os.path.isfile(asset):
            return self.upload(asset)
        return asset

    def generate(self, prompt: str, out: str = "minimax-h3.mp4", **options: Any) -> Dict[str, Any]:
        """Validate, upload any local files, submit, wait, download. Returns the
        finished job; job['usage'] holds output, reference and billed seconds."""
        build_request(prompt, **_with_placeholders(options))  # fail before uploading anything
        for key in ("start_image", "end_image"):
            if options.get(key) is not None:
                options[key] = self._resolve(options[key])
        for key in ("reference_images", "reference_videos", "reference_audios"):
            options[key] = [self._resolve(a) for a in options.get(key) or ()]
        request_id = self.submit(build_request(prompt, **options))
        print(f"submitted {request_id}", file=sys.stderr)
        job = self.wait(request_id, on_update=_print_progress)
        self.download(request_id, out)
        return job


def _print_progress(job: Dict[str, Any]) -> None:
    left = job.get("estimated_seconds_remaining")
    eta = f", about {left}s left" if left is not None else ""
    print(f"{job.get('status')} {job.get('progress', 0)}%{eta}", file=sys.stderr)


def _placeholder(asset: Optional[Asset]) -> Optional[Asset]:
    """Stand in for a local file without uploading it (validation, --dry-run)."""
    if not isinstance(asset, str) or asset.startswith("https://"):
        return asset
    return "https://upload.invalid/" + os.path.basename(asset)


def _with_placeholders(options: Dict[str, Any]) -> Dict[str, Any]:
    preview = dict(options)
    for key in ("start_image", "end_image"):
        preview[key] = _placeholder(preview.get(key))
    for key in ("reference_images", "reference_videos", "reference_audios"):
        preview[key] = [_placeholder(a) for a in preview.get(key) or ()]
    return preview


def main(argv: Optional[List[str]] = None) -> int:
    cli = argparse.ArgumentParser(description="Generate one MiniMax H3 video.")
    cli.add_argument("prompt")
    cli.add_argument("--duration", type=int, default=5, help="4-15 seconds (default 5)")
    cli.add_argument("--resolution", default="768p", choices=sorted(PRICE_PER_SECOND))
    cli.add_argument("--ratio", dest="aspect_ratio", help="21:9 16:9 4:3 1:1 3:4 9:16, or adaptive with references")
    cli.add_argument("--first", dest="start_image", help="first frame: local JPEG/PNG or https URL")
    cli.add_argument("--last", dest="end_image", help="last frame (needs --first)")
    cli.add_argument("--image", action="append", default=[], help="reference image, repeatable (@Image 1, 2, ...)")
    cli.add_argument("--video", action="append", default=[], help="reference clip, 2-15 s, repeatable")
    cli.add_argument("--audio", action="append", default=[], help="reference audio, 2-15 s, repeatable")
    cli.add_argument("--ref-video-seconds", type=float, default=0.0, help="total reference-video length, for the estimate")
    cli.add_argument("--out", default="minimax-h3.mp4")
    cli.add_argument("--dry-run", action="store_true", help="validate and price only; nothing is sent")
    args = cli.parse_args(argv)

    options = dict(duration=args.duration, resolution=args.resolution, aspect_ratio=args.aspect_ratio,
                   start_image=args.start_image, end_image=args.end_image, reference_images=args.image,
                   reference_videos=args.video, reference_audios=args.audio)
    cost = estimate_cost(args.duration, args.resolution, args.ref_video_seconds)
    try:
        if args.dry_run:
            print(json.dumps(build_request(args.prompt, **_with_placeholders(options)), indent=2))
            print(f"estimated cost: ${cost:.3f}", file=sys.stderr)
            return 0
        job = MiniMaxH3().generate(args.prompt, args.out, **options)
    except H3Error as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(json.dumps({"file": args.out, "usage": job.get("usage", {})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
