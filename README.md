# Meta AI Video Generation Automation

This repository automates the process of generating videos using Meta AI (`https://meta.ai/`) via Playwright and GitHub Actions. It is designed to be triggered by an `n8n` workflow and will send the result back to an `n8n` webhook.

## How it works

1. Your `n8n` workflow sends an HTTP POST request to the GitHub API to trigger the Action.
2. The payload includes:
   - `prompt`: The text to generate the video (e.g., "generate a video of a cat playing piano").
   - `webhook_url`: The URL of your n8n webhook where the result should be sent.
   - `cookies_b64`: Your Netscape formatted cookies string encoded in Base64 (used to bypass login/captcha).
3. GitHub Actions spins up a headless browser, injects the cookies, goes to `meta.ai`, types the prompt, and waits for the video.
4. Once the video URL is found, it sends a POST request to your `webhook_url`.

## Setup

1. **Push to GitHub**: Push these files to a GitHub repository.
2. **GitHub Token**: You need a Personal Access Token (PAT) with `repo` permissions to trigger the workflow from n8n.

## How to trigger from n8n

Use the **HTTP Request** node in n8n with the following settings:

- **Method**: `POST`
- **URL**: `https://api.github.com/repos/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>/dispatches`
- **Authentication**: Header Auth
  - Name: `Authorization`
  - Value: `Bearer YOUR_GITHUB_PERSONAL_ACCESS_TOKEN`
- **Headers**:
  - `Accept`: `application/vnd.github.v3+json`
- **Body Parameters**: JSON
```json
{
  "event_type": "generate_video",
  "client_payload": {
    "prompt": "generate a video of a fast car",
    "webhook_url": "https://alisaadeng-n8n.hf.space/webhook-test/...",
    "cookies_b64": "IyBOZXRzY2FwZSBIVFRQIENvb2tpZSBGaWxlCg=="
  }
}
```

## Receiving the Webhook in n8n

Create a **Webhook** node in n8n to receive the result. The payload sent by the script will look like this:

```json
{
  "success": true,
  "prompt": "generate a video of a fast car",
  "result": "https://scontent.xx.fbcdn.net/v/t39.10537-6/..." 
}
```
If `success` is `false`, the `result` will contain the error message instead of the URL.
