# 🎬 Meta AI Video Generation API

A fully working **Video Generation API** built on top of Meta AI, powered by Playwright browser automation, GitHub Actions, n8n workflows, and Redis — no dedicated server required.

Send a prompt → get back 4 AI-generated videos. That's it.

---

## 🏗️ Architecture Overview

```
Client (POST /video-genrate)
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  n8n Workflow                                           │
│                                                         │
│  FLOW 1 — Cookie Setup (one-time)                       │
│  POST /set-cookies                                      │
│    → Encode Cookies (JS)                                │
│    → Redis SET  key: meta_cookies_b64                   │
│                                                         │
│  FLOW 2 — Video Generation (main)                       │
│  POST /video-genrate                                    │
│    → Generate Job ID (UUID)                             │
│    → Redis GET  key: meta_cookies_b64                   │
│    → GitHub Actions trigger (repository_dispatch)       │
│    → Respond 202 { job_id, message }   ◄── immediate   │
│                                                         │
│  FLOW 3 — Receive Result (callback)                     │
│  POST /received-video  ◄── called by GitHub Action      │
│    → If success == true                                 │
│    → Redis SET  key: job_id → JSON(body)                │
└─────────────────────────────────────────────────────────┘
        │
        ▼
GitHub Actions (ubuntu-latest)
    → Install Python + Playwright
    → Decode cookies from Redis
    → Open meta.ai headlessly
    → Type prompt & submit
    → Wait for 4 videos
    → POST results back to /received-video
```

---

## ✨ Features

- 🤖 **Headless browser automation** — Playwright controls Meta AI like a real user
- ☁️ **Serverless execution** — Runs entirely on GitHub Actions (free tier)
- ⚡ **Async by design** — API responds immediately with `job_id`, generation runs in background
- 🔗 **Webhook callback** — Results sent back to n8n automatically when ready
- 🗄️ **Redis storage** — Cookies and video results stored by key for retrieval
- 🍪 **Cookie-based auth** — Supports Netscape cookie format, auto-filtered for `meta.ai` domain
- 🧩 **Job tracking** — Every request gets a unique UUID `job_id`

---

## 📁 Project Structure

```
├── meta_ai_bot.py               # Playwright automation script
├── requirements.txt             # Python dependencies
├── .github/
│   └── workflows/
│       └── generate_video.yml  # GitHub Actions workflow
└── README.md
```

---

## 🚀 Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### 2. Add GitHub Token to n8n

In your n8n HTTP Bearer credential, add a GitHub Personal Access Token with `repo` scope — used to trigger `repository_dispatch`.

### 3. Set up Redis

Any Redis instance works (local, Railway, Upstash, etc.). Add the connection in n8n under **Credentials → Redis**.

### 4. Import the n8n Workflow

Import the workflow JSON into n8n. It contains 3 flows:

| Flow | Endpoint | Purpose |
|------|----------|---------|
| Cookie Setup | `POST /set-cookies` | One-time cookie upload |
| Video Generate | `POST /video-genrate` | Trigger generation |
| Receive Result | `POST /received-video` | GitHub Action callback |

---

## 🍪 Flow 1 — Set Cookies (One-Time Setup)

Before generating videos, upload your Meta AI cookies once. They get stored in Redis and reused for every request.

**Endpoint:** `POST /set-cookies`

```json
{
  "cookies_txt": "# Netscape HTTP Cookie File\n.meta.ai\tTRUE\t/\tTRUE\t..."
}
```

The workflow will:
1. Parse the Netscape cookie file
2. Filter only `meta.ai` domain cookies
3. Convert to base64
4. Store in Redis under key `meta_cookies_b64`

---

## 🎬 Flow 2 — Generate Video

**Endpoint:** `POST /video-genrate`

```json
{
  "prompt": "a magical forest with glowing trees at night"
}
```

> ⚠️ **Important — Prompt Formatting:**
> The prompt is automatically wrapped before being sent to Meta AI:
> ```
> generate video about [a magical forest with glowing trees at night]
> ```
> This prefix **`generate video about`** is required for Meta AI to correctly understand the generation intent. The workflow adds it automatically — do **not** include it in your request.

**Response (202 Accepted):**

```json
{
  "success": true,
  "job_id": "d70e01fd-22d4-4b9e-abe1-c30c7144c67d",
  "message": "Job queued successfully"
}
```

The response is immediate. Actual generation takes ~2–3 minutes on GitHub Actions.

---

## 📬 Flow 3 — Receive Result (Callback)

When GitHub Actions finishes, it sends results back automatically to `/received-video` (internal endpoint, called by the Python script).

**Payload received:**

```json
{
  "job_id": "d70e01fd-22d4-4b9e-abe1-c30c7144c67d",
  "success": true,
  "prompt": "generate video about [a magical forest with glowing trees at night]",
  "video_urls": [
    "https://scontent-ord5-1.xx.fbcdn.net/...video1.mp4",
    "https://scontent-ord5-2.xx.fbcdn.net/...video2.mp4",
    "https://scontent-ord5-3.xx.fbcdn.net/...video3.mp4",
    "https://scontent-ord5-1.xx.fbcdn.net/...video4.mp4"
  ],
  "video_count": 4,
  "error": null
}
```

If `success == true`, the full body is stored in Redis:
```
KEY:   d70e01fd-22d4-4b9e-abe1-c30c7144c67d
VALUE: JSON.stringify(body)
```

---

## 🔍 Retrieving Results

Poll Redis using the `job_id` returned from the generate endpoint:

```javascript
// n8n Redis GET node → Key: your job_id

// Extract video URLs after GET:
={{ JSON.parse($json.value).video_urls }}
```

---

## 🗄️ Redis Key Map

| Key | Value | Set by |
|-----|-------|--------|
| `meta_cookies_b64` | base64 encoded Meta AI cookies | Flow 1 |
| `{job_id}` | Full JSON response body (stringified) | Flow 3 |

---

## ⏱️ Timing

| Step | Duration |
|------|----------|
| API response (`job_id`) | ~instant |
| GitHub Actions queue | ~10–30 sec |
| Browser automation + generation | ~2–3 min |
| **Total time to videos** | **~3–4 min** |

---

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `No video URLs found` | Cookies expired — re-run Flow 1 with fresh cookies |
| `Failed to navigate` | Meta AI may be down or rate-limiting |
| `Invalid argument type` in Redis | Wrap value with `JSON.stringify()` |
| GitHub Action not triggered | Check Bearer token has `repo` scope |
| Webhook not receiving results | Verify n8n instance is publicly accessible |

---

## 🔮 Roadmap — Upcoming Features

### 🖼️ Image to Video
Convert a static image into a short animated video. The pipeline will accept an image URL or base64 input, upload it to Meta AI, and return the generated video.

### ✍️ Text to Video *(Enhanced)*
Improve the current flow with support for style parameters, aspect ratio selection, duration control, and prompt templates for more consistent output quality.

### 🎞️ Video to Video
Use an existing video as a reference and apply a new prompt on top of it to transform the style, motion, or content — similar to img2img but for video.

### 🎨 Animate (Image Animation)
Bring still images to life by applying motion to them. Input a photo and a motion prompt, get back an animated video clip — great for portraits, product shots, and landscapes.

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

## 🙏 Built With

[Playwright](https://playwright.dev/) · [n8n](https://n8n.io/) · [GitHub Actions](https://github.com/features/actions) · [Redis](https://redis.io/)
