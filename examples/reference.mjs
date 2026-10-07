// Node 18+ (built-in fetch): upload a local image, run a reference job, save the MP4.
//   MINIMAX_H3_API_KEY=... node examples/reference.mjs ./product.png
import { readFile, writeFile, stat } from "node:fs/promises";

const BASE = (process.env.MINIMAX_H3_BASE_URL ?? "https://argolink.io/v1").replace(/\/+$/, "");
const KEY = process.env.MINIMAX_H3_API_KEY;
if (!KEY) throw new Error("export MINIMAX_H3_API_KEY first");
const auth = { Authorization: `Bearer ${KEY}` };

async function call(path, init = {}) {
  const res = await fetch(BASE + path, { ...init, headers: { ...auth, ...init.headers } });
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path}: HTTP ${res.status} ${await res.text()}`);
  return res;
}

async function upload(file, contentType) {
  const { size } = await stat(file);
  const ticket = await (await call("/media/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "minimax-h3", type: "image", content_type: contentType, size_bytes: size }),
  })).json();
  const put = await fetch(ticket.upload_url, { method: "PUT", headers: { "Content-Type": contentType }, body: await readFile(file) });
  if (!put.ok) throw new Error(`upload failed: HTTP ${put.status}`);
  return ticket.media_url; // readable for seven days
}

const file = process.argv[2] ?? "product.png";
const imageUrl = await upload(file, file.endsWith(".png") ? "image/png" : "image/jpeg");

const { request_id } = await (await call("/videos/generations", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "minimax-h3",
    prompt: "@Image 1 turns slowly on a marble pedestal as soft light sweeps across it, studio product film",
    duration: 5,
    resolution: "768p",
    aspect_ratio: "1:1",
    reference_images: [{ url: imageUrl }],
  }),
})).json();

let job;
for (;;) {
  job = await (await call(`/videos/${request_id}`)).json();
  if (job.status === "done") break;
  if (job.status === "failed" || job.status === "expired") throw new Error(JSON.stringify(job.error ?? job));
  console.error(`${job.status} ${job.progress ?? 0}%`);
  await new Promise((r) => setTimeout(r, 10_000));
}
const video = await call(`/videos/${request_id}/content`);
await writeFile("product.mp4", Buffer.from(await video.arrayBuffer()));
console.log(job.usage); // 5 s at 768p bills $0.195; the reference image is free
