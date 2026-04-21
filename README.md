# Meta AI Automation (Video & Image Generation)

This repository automates the process of generating images and videos using Meta AI (`https://meta.ai/`) via Playwright and GitHub Actions. It is designed to be triggered by an `n8n` workflow and will send the result back to an `n8n` webhook.

## Supported Actions

You can pass an `action` parameter to control exactly what the bot does:

1. `text_to_video` (Default): Generates an animated video directly from your prompt (extracts up to 4 video variations).
2. `text_to_image`: Generates an image from your prompt and returns the image URLs.
3. `animate_generation`: Generates an image first, clicks the "Animate" button on the image, and returns the video URL.
4. `image_to_video`: Uploads a specific image from a URL you provide, and asks Meta AI to animate/edit it into a video.

## How to trigger from n8n

Use the **HTTP Request** node in n8n with the following settings:

- **Method**: `POST`
- **URL**: `https://api.github.com/repos/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>/dispatches`
- **Authentication**: Header Auth (Personal Access Token)
- **Headers**:
  - `Accept`: `application/vnd.github.v3+json`
- **Body Parameters**: JSON

### 1. Generating a Video (text_to_video)
```json
{
  "event_type": "generate_video",
  "client_payload": {
    "prompt": "generate a video of a fast car",
    "webhook_url": "YOUR_N8N_WEBHOOK_URL",
    "job_id": "123",
    "cookies_b64": "IyBOZXRzY2FwZ...",
    "action": "text_to_video"
  }
}
```

### 2. Generating an Image (text_to_image)
*💡 Tip for Aspect Ratios: Meta AI understands text commands for dimensions. You can control the aspect ratio by simply adding it to the end of your prompt.*
```json
{
  "event_type": "generate_video",
  "client_payload": {
    "prompt": "a futuristic city, in 16:9 aspect ratio",
    "webhook_url": "YOUR_N8N_WEBHOOK_URL",
    "job_id": "123",
    "cookies_b64": "IyBOZXRzY2FwZ...",
    "action": "text_to_image"
  }
}
```

### 3. Generate Image & Animate it (animate_generation)
*💡 Tip: Use this if you want to ensure the video retains a specific aspect ratio or high-quality composition. You ask for a 16:9 image, then the script automatically animates it!*
```json
{
  "event_type": "generate_video",
  "client_payload": {
    "prompt": "a cute cat in space, 9:16 aspect ratio",
    "webhook_url": "YOUR_N8N_WEBHOOK_URL",
    "job_id": "123",
    "cookies_b64": "IyBOZXRzY2FwZ...",
    "action": "animate_generation"
  }
}
```

### 4. Upload an Image to Video (image_to_video)
```json
{
  "event_type": "generate_video",
  "client_payload": {
    "prompt": "Animate this image",
    "webhook_url": "YOUR_N8N_WEBHOOK_URL",
    "job_id": "123",
    "cookies_b64": "IyBOZXRzY2FwZ...",
    "action": "image_to_video",
    "image_url": "https://example.com/your-image.jpg"
  }
}
```

## Receiving the Webhook in n8n

Create a **Webhook** node in n8n to receive the result. The payload sent by the script is backwards-compatible but adds new useful fields.

**For Videos:**
```json
{
  "job_id": "123",
  "success": true,
  "prompt": "generate a video of a fast car",
  "media_type": "video",
  "video_urls": [
    "https://scontent...mp4",
    "https://scontent...mp4"
  ],
  "video_count": 2,
  "error": null
}
```

**For Images:**
```json
{
  "job_id": "123",
  "success": true,
  "prompt": "a futuristic city, in 16:9 aspect ratio",
  "media_type": "image",
  "image_urls": [
    "https://scontent...jpg"
  ],
  "image_count": 1,
  "video_urls": [
    "https://scontent...jpg"
  ],
  "video_count": 1,
  "error": null
}
```
*(Note: `video_urls` is included even for images to prevent breaking older n8n workflows that explicitly look for the `video_urls` key).*

If `success` is `false`, the `error` field will contain the error message, and a screenshot will be saved in your GitHub Actions logs for debugging.
