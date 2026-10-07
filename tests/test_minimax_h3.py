"""Offline tests. A fake urlopen plays the API, so no key, network or money is needed."""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import minimax_h3 as h3  # noqa: E402


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeAPI:
    """Routes requests by URL and records them."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        url, method = request.full_url, request.get_method()
        if url.endswith("/videos/generations") and method == "POST":
            return Reply(json.dumps({"request_id": "job-1"}).encode())
        if url.endswith("/videos/job-1/content"):
            return Reply(b"\x00\x00\x00\x18ftypmp42")
        if url.endswith("/videos/job-1"):
            return Reply(json.dumps(self.statuses.pop(0)).encode())
        if url.endswith("/media/uploads"):
            return Reply(json.dumps({"upload_url": "https://storage.test/put/abc",
                                     "media_url": "https://media.test/abc.png"}).encode())
        if url == "https://storage.test/put/abc" and method == "PUT":
            return Reply(b"")
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def api(monkeypatch):
    def install(statuses=()):
        fake = FakeAPI(statuses)
        monkeypatch.setattr(h3.urllib.request, "urlopen", fake)
        monkeypatch.setattr(h3.time, "sleep", lambda seconds: None)
        return fake
    return install


def test_text_request_defaults_to_768p_16_9():
    assert h3.build_request("a fox in snow") == {
        "model": "minimax-h3", "prompt": "a fox in snow", "duration": 5,
        "resolution": "768p", "aspect_ratio": "16:9",
    }


@pytest.mark.parametrize("tier", ["720p", "1080p", "480p", "4k"])
def test_tiers_other_than_768p_and_2k_are_refused_locally(tier):
    with pytest.raises(h3.H3Error, match="768p or 2k"):
        h3.build_request("x", resolution=tier)


@pytest.mark.parametrize("seconds", [3, 16, 5.5, True])
def test_duration_must_be_whole_seconds_from_4_to_15(seconds):
    with pytest.raises(h3.H3Error):
        h3.build_request("x", duration=seconds)


def test_frame_mode_is_adaptive_and_excludes_references():
    body = h3.build_request("day to night", start_image="https://a.test/1.png", end_image="https://a.test/2.png")
    assert body["aspect_ratio"] == "adaptive"
    assert body["end_image"] == {"url": "https://a.test/2.png"}
    with pytest.raises(h3.H3Error, match="cannot be combined"):
        h3.build_request("x", start_image="https://a.test/1.png", reference_images=["https://a.test/r.png"])
    with pytest.raises(h3.H3Error, match="adaptive"):
        h3.build_request("x", start_image="https://a.test/1.png", aspect_ratio="16:9")
    with pytest.raises(h3.H3Error, match="needs a start_image"):
        h3.build_request("x", end_image="https://a.test/2.png")


def test_reference_rules():
    with pytest.raises(h3.H3Error, match="audio needs"):
        h3.build_request("x", reference_audios=["https://a.test/beat.mp3"])
    with pytest.raises(h3.H3Error, match="12 reference assets"):
        h3.build_request("x", reference_images=["https://a.test/i.png"] * 9,
                         reference_videos=["https://a.test/v.mp4"] * 3, reference_audios=["https://a.test/a.mp3"])
    with pytest.raises(h3.H3Error, match="adaptive"):
        h3.build_request("x", aspect_ratio="adaptive")  # adaptive needs assets to follow
    body = h3.build_request("x", aspect_ratio="adaptive", reference_videos=["https://a.test/v.mp4"])
    assert body["reference_videos"] == [{"url": "https://a.test/v.mp4"}]


def test_cost_adds_whole_reference_video_seconds():
    assert h3.estimate_cost(5, "768p", reference_video_seconds=2.4) == pytest.approx(7 * 0.039)
    assert h3.estimate_cost(15, "2k") == pytest.approx(0.96)


def test_generate_runs_submit_poll_download(api, tmp_path):
    fake = api([{"status": "pending", "progress": 40, "estimated_seconds_remaining": 60},
                {"status": "done", "progress": 100,
                 "usage": {"output_seconds": 6, "reference_video_seconds": 0, "billed_seconds": 6}}])
    out = tmp_path / "clip.mp4"
    job = h3.MiniMaxH3(api_key="k").generate("waves at dusk", str(out), duration=6, resolution="2k")

    submit = fake.requests[0]
    assert submit.full_url == "https://argolink.io/v1/videos/generations"
    assert submit.get_header("Authorization") == "Bearer k"
    assert json.loads(submit.data)["resolution"] == "2k"
    assert fake.requests[-1].full_url.endswith("/videos/job-1/content")
    assert out.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")
    assert job["usage"]["billed_seconds"] == 6


def test_local_reference_is_uploaded_with_its_content_type(api, tmp_path):
    fake = api([{"status": "done", "usage": {"billed_seconds": 5}}])
    image = tmp_path / "hero.png"
    image.write_bytes(b"\x89PNG....")
    h3.MiniMaxH3(api_key="k").generate("@Image 1 waves", str(tmp_path / "o.mp4"), reference_images=[str(image)])

    ticket = json.loads(fake.requests[0].data)
    assert ticket == {"model": "minimax-h3", "type": "image", "content_type": "image/png", "size_bytes": 8}
    put = fake.requests[1]
    assert put.get_method() == "PUT" and put.get_header("Content-type") == "image/png"
    assert put.get_header("Authorization") is None  # the signed URL carries its own auth
    assert json.loads(fake.requests[2].data)["reference_images"] == [{"url": "https://media.test/abc.png"}]


@pytest.mark.parametrize("state", ["failed", "expired"])
def test_unfinished_jobs_raise_with_the_error(api, state):
    api([{"status": state, "error": {"code": "content_policy_violation", "retryable": False}}])
    with pytest.raises(h3.H3Error, match="content_policy_violation") as caught:
        h3.MiniMaxH3(api_key="k").wait("job-1")
    assert caught.value.payload["status"] == state


def test_http_error_keeps_status_and_code(monkeypatch):
    def refuse(request, timeout=None):
        body = b'{"error":{"type":"invalid_request_error","code":"request_unsupported","message":"minimax-h3 does not support video resolution 720p"}}'
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(h3.urllib.request, "urlopen", refuse)
    with pytest.raises(h3.H3Error, match="request_unsupported") as caught:
        h3.MiniMaxH3(api_key="k").submit({"model": "minimax-h3", "prompt": "x", "resolution": "720p"})
    assert caught.value.status == 400


def test_dry_run_prints_body_without_network(monkeypatch, capsys):
    monkeypatch.setattr(h3.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    assert h3.main(["@Image 1 smiles", "--image", "face.png", "--duration", "4", "--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["reference_images"] == [{"url": "https://upload.invalid/face.png"}]


def test_key_and_base_url_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("MINIMAX_H3_API_KEY", raising=False)
    with pytest.raises(h3.H3Error):
        h3.MiniMaxH3()
    monkeypatch.setenv("MINIMAX_H3_API_KEY", "k")
    monkeypatch.setenv("MINIMAX_H3_BASE_URL", "https://gateway.test/v1/")
    assert h3.MiniMaxH3().base_url == "https://gateway.test/v1"
