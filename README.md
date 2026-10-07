# MiniMax H3 API — from $0.039/s (768p)

**MiniMax H3 is live on [ArgoLink](https://argolink.io/gh-minimax-h3-api)**: $0.039/s at 768p and $0.064/s at 2K, 51% below MiniMax's own pay-as-you-go price. A prompt, a first frame, a first and last frame, or up to 12 reference images, clips and sounds go in; a 4–15 second MP4 with a generated stereo soundtrack comes out, from one async endpoint billed per second.

[MiniMax H3 model page →](https://argolink.io/en/models/minimax-h3) · [Prices](#price-per-second) · [Quick start](#quick-start) · [Modes](#modes) · [Billing](#billing-rules) · [Errors](#status-and-errors) · [FAQ](#faq)

What is in this repo: [`minimax_h3.py`](./minimax_h3.py), one dependency-free Python file that checks a request against the model's limits before anything is spent, uploads local files, submits, polls and saves the MP4; [examples](./examples) for every mode in Python, Node.js and curl; and offline [tests](./tests). Any server that speaks the same `/v1/videos` protocol works: set `MINIMAX_H3_BASE_URL`.

<sub>Last checked: 2026-10-07, against the model page and the provider pages linked under each table.</sub>

## At a glance

| | |
|---|---|
| Model ID | `minimax-h3` — one ID for every mode |
| Calls | `POST /v1/videos/generations` → `GET /v1/videos/{request_id}` → `GET /v1/videos/{request_id}/content` |
| Modes | text · first frame · first + last frame · reference (images, video, audio) |
| Duration | 4–15 s in whole seconds, default 5 |
| Resolution | `768p` (default) or `2k`. There is no 480p, 720p or 1080p tier |
| Aspect ratio | 21:9 · 16:9 · 4:3 · 1:1 · 3:4 · 9:16 (default 16:9). Reference jobs may also send `adaptive`; frame modes accept `adaptive` only |
| References | ≤ 9 images, ≤ 3 videos, ≤ 3 audio clips, ≤ 12 in total. Clips 2–15 s each and 15 s per kind together. Audio needs an image or video beside it |
| Prompt | required in every mode, up to 7,000 characters; point at assets with `@Image 1`, `@Video 1`, `@Audio 1` |
| Sound | always on: each MP4 carries a model-generated AAC stereo track; `generate_audio: false` is rejected |
| Output | one 24 fps H.264 MP4 per request |
| Rejected with 400 | `seed`, `watermark: true`, `n` other than 1 |

## Price per second

One second of output, no reference video. Each provider's own figure.

| Provider | 768p | 2K | 10 s at 768p | Reference images |
|---|---:|---:|---:|---|
| **[ArgoLink](https://argolink.io/en/models/minimax-h3)** | **$0.039** | **$0.064** | **$0.39** | free |
| [MiniMax pay-as-you-go](https://platform.minimax.io/docs/guides/pricing-paygo) (official) | $0.08 | $0.13 | $0.80 | first 5 free, then $0.04 each |
| [fal.ai](https://fal.ai/models/minimax/h3/text-to-video) | $0.06 | $0.13 | $0.60 | first 5 free, then $0.08 each |
| [Replicate](https://replicate.com/minimax/h3) | $0.08 | $0.13 | $0.80 | not stated |

<sub>All four pages read 2026-10-07. fal also lists 480p ($0.05/s) and 4K ($0.16/s) tiers, which the ArgoLink route does not offer. MiniMax additionally sells a 768p→2K regeneration at $0.05/s; here you render 2K directly. Re-check the linked pages before you budget.</sub>

## Quick start

```bash
git clone https://github.com/duncangrimsby/minimax-h3-api
cd minimax-h3-api
export MINIMAX_H3_API_KEY=your-key   # created in the ArgoLink console

python3 minimax_h3.py "A paper boat drifts down a rain gutter, low tracking shot" --duration 6 --out boat.mp4
python3 minimax_h3.py "The cat in @Image 1 naps in a sunbeam" --image cat.png --ratio 1:1 --dry-run
```

`--dry-run` validates the request and prints the JSON body and its estimated cost without sending anything. A real run prints progress, saves the MP4 and prints the billed seconds.

### curl

```bash
BASE=https://argolink.io/v1

# 1. Submit. Keep request_id: it is the only handle for the next two calls.
curl $BASE/videos/generations \
  -H "Authorization: Bearer $MINIMAX_H3_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "minimax-h3", "prompt": "A slow aerial shot over a foggy pine forest at sunrise",
       "duration": 5, "resolution": "768p", "aspect_ratio": "16:9"}'

# 2. Poll every 10 s or so: pending (progress 0-99), then done, failed or expired
curl $BASE/videos/$REQUEST_ID -H "Authorization: Bearer $MINIMAX_H3_API_KEY"

# 3. Download once done (409 job_video_not_ready before that, 424 job_failed for a failed job)
curl -L $BASE/videos/$REQUEST_ID/content -H "Authorization: Bearer $MINIMAX_H3_API_KEY" -o forest.mp4
```

### Python

```python
from minimax_h3 import MiniMaxH3, build_request, estimate_cost

body = build_request("Rain on a tin roof, macro shot", duration=8, resolution="2k")  # raises before you pay
print(estimate_cost(8, "2k"))                                                       # 0.512

client = MiniMaxH3()                        # MINIMAX_H3_API_KEY, optional MINIMAX_H3_BASE_URL
request_id = client.submit(body)
job = client.wait(request_id)               # raises H3Error on failed / expired
client.download(request_id, "rain.mp4")
print(job["usage"])                         # output_seconds, reference_video_seconds, billed_seconds
```

`client.generate(prompt, "out.mp4", **options)` does all four steps and uploads any local paths you pass as frames or references.

### Node.js

[`examples/reference.mjs`](./examples/reference.mjs) uploads a local product shot and turns it into a square turntable clip with nothing but `fetch`:

```bash
MINIMAX_H3_API_KEY=... node examples/reference.mjs ./product.png
```

## Modes

The fields you send choose the mode; the endpoint and model ID stay the same.

| Mode | Send | Rules | Example |
|---|---|---|---|
| Text | `prompt` | fixed ratio only | [text_to_video.py](./examples/text_to_video.py) |
| First frame | `start_image` | `aspect_ratio` `adaptive` or left out; no references | `--first frame.png` |
| First + last frame | `start_image` + `end_image` | the video follows the first frame's shape; the last frame may differ in ratio | [first_last_frame.sh](./examples/first_last_frame.sh) |
| Reference | `reference_images`, `reference_videos`, `reference_audios` | any mix up to 9 / 3 / 3 and 12 in total, at least one image or video | [reference_mix.py](./examples/reference_mix.py) |

A reference request with everything at once:

```json
{
  "model": "minimax-h3",
  "prompt": "The girl in @Image 1 dances to the beat of @Audio 1, moving like the dancer in @Video 1",
  "duration": 10,
  "resolution": "768p",
  "reference_images": [{"url": "https://example.com/girl.png"}],
  "reference_videos": [{"url": "https://example.com/dance.mp4"}],
  "reference_audios": [{"url": "https://example.com/beat.mp3"}]
}
```

`@Image 1` is the first entry of `reference_images`, `@Video 2` the second of `reference_videos`, and so on. Naming is optional (unnamed assets are still used), but a number past the end of its list is rejected.

### Local files

Every asset is an HTTPS URL that downloads without signing in. For a local file, ask for an upload ticket, PUT the bytes, then use the returned `media_url`:

```bash
TICKET=$(curl -fsS $BASE/media/uploads -H "Authorization: Bearer $MINIMAX_H3_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"minimax-h3\",\"type\":\"video\",\"content_type\":\"video/mp4\",\"size_bytes\":$(wc -c < moves.mp4 | tr -d ' ')}")
curl -fsS -X PUT "$(jq -r .upload_url <<<"$TICKET")" -H "Content-Type: video/mp4" --data-binary @moves.mp4
jq -r .media_url <<<"$TICKET"   # goes into reference_videos[].url
```

| Field | Format | Max size | Length |
|---|---|---|---|
| `reference_images`, `start_image`, `end_image` | JPEG, PNG | 20 MiB | — |
| `reference_videos` | MP4, MOV | 100 MiB | 2–15 s each |
| `reference_audios` | WAV, MP3 | 15 MB | 2–15 s each |

The ticket is free and creates no job. `upload_url` lives 15 minutes and the PUT must carry the declared `Content-Type`; `media_url` stays readable for seven days. An asset outside these limits fails the job with `invalid_input` before generation, at no charge.

## Billing rules

- **Per output second** at the tier rate: $0.039 (768p) or $0.064 (2K).
- **Reference video seconds are added**, with any fraction of the total dropped: a 5 s video with a 2.4 s reference clip bills 7 s.
- **Reference images and audio are free**, however many you send within the limits.
- **Failed, rejected or expired jobs cost nothing.** Only a finished video is billed.
- The finished job's `usage` reports `output_seconds`, `reference_video_seconds` and `billed_seconds`.

| Job | Billed seconds | Cost |
|---|---:|---:|
| 4 s, 768p, prompt only | 4 | $0.156 |
| 10 s, 768p, three reference images | 10 | $0.39 |
| 8 s, 768p, one 5 s reference clip | 13 | $0.507 |
| 15 s, 2K, first + last frame | 15 | $0.96 |

## Status and errors

| Status | Meaning |
|---|---|
| `pending` | generating; `progress` 0–99 and, once known, `estimated_seconds_remaining` |
| `done` | `progress` 100, `video.url` (same as the content endpoint, needs your key) and `usage` |
| `failed` | nothing produced, nothing billed; `error.code`, `error.message`, `error.retryable` |
| `expired` | nothing produced, nothing billed |

The video endpoint documents one error envelope, `{"error": {"type", "code", "param", "message"}}`. The ones you will meet: 400 `request_unsupported` (a 720p tier, a seed, audio-only references, too many assets), 401 bad key, 402/403 balance or permission, 404 `video_request_not_found` (unknown ID or another user's job), 409 `job_video_not_ready`, 424 `job_failed`, 429 rate limit, 503 no capacity (retry after `Retry-After`; nothing is charged).

## Field names from other APIs

Requests written for fal or OpenRouter mostly work as they are: `image_url` means `start_image`; `image_urls`, `video_urls` and `audio_urls` are the three reference lists; `frame_images` and `input_references` items map to frames and references; `seconds` and `ratio` alias `duration` and `aspect_ratio`. Send one name per input. `size` in pixels can replace `resolution` + `aspect_ratio` (`1366x768` is 768p 16:9).

## FAQ

**How much does a 10-second MiniMax H3 video cost?**
$0.39 at 768p or $0.64 at 2K on ArgoLink, plus the length of any reference clips. The same 10 seconds list at $0.80 and $1.30 on MiniMax's own platform.

**Why was my 720p request rejected?**
MiniMax H3 has two tiers, `768p` and `2k`. Anything else, including `720p` and `1080p`, returns 400 before a job is created.

**Can I get a silent video?**
Not from the API: every H3 video carries a generated stereo track. Strip it after download with `ffmpeg -i in.mp4 -an -c:v copy out.mp4`.

**Why was my audio reference rejected?**
Audio cannot be the only reference on MiniMax H3. Add at least one reference image or video.

**How is the shape of a first-frame video chosen?**
From the first frame: send `aspect_ratio: "adaptive"` or leave it out, and the closest of the six ratios is used. A fixed ratio in a frame mode is rejected.

**Is MiniMax H3 Max available here?**
No. On 2026-10-07 `GET https://argolink.io/v1/models` lists `minimax-h3` only.

**Can I fix a seed to repeat a render?**
No. `seed` returns 400 on this model.

## Run the tests

```bash
pip install pytest
pytest tests/
```

The tests replace the HTTP layer, so they need no key and make no network calls.

## License

MIT
