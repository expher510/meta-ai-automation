import argparse
import time
import requests
import sys
import os
import re
import json
import base64
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_netscape_cookies(file_path_or_content: str) -> list[dict]:
    """Parse cookies from a file path, JSON string, or Netscape text."""
    content = ""
    try:
        with open(file_path_or_content, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = file_path_or_content

    # Try JSON first
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Netscape / tab-separated format
    cookies: list[dict] = []
    for line in content.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 7:
            cookie: dict = {
                "domain": parts[0],
                "path": parts[2],
                "secure": parts[3].lower() == "true",
                "name": parts[5],
                "value": parts[6],
            }
            try:
                expires = float(parts[4])
                if expires > 0:
                    cookie["expires"] = expires
            except ValueError:
                pass
            cookies.append(cookie)
    return cookies


def send_to_webhook(
    webhook_url: str,
    media_urls: list[str],
    prompt: str,
    action_type: str,
    success: bool = True,
    error: str | None = None,
    job_id: str | None = None,
    retries: int = 3,
) -> None:
    """POST result to webhook with exponential back-off retry."""
    if not webhook_url:
        print("[webhook] No URL provided — skipping.")
        return

    is_video = action_type in ("text_to_video", "animate_generation", "image_to_video")
    payload = {
        "job_id": job_id,
        "success": success,
        "prompt": prompt,
        "media_type": "video" if is_video else "image",
        "error": error,
        "video_urls": media_urls if is_video else [],
        "video_count": len(media_urls) if is_video else 0,
        "image_urls": media_urls if not is_video else [],
        "image_count": len(media_urls) if not is_video else 0,
    }

    for attempt in range(1, retries + 1):
        try:
            print(f"[webhook] Attempt {attempt}/{retries} → {webhook_url}")
            resp = requests.post(webhook_url, json=payload, timeout=30)
            resp.raise_for_status()
            print(f"[webhook] OK — HTTP {resp.status_code}")
            return
        except Exception as exc:
            print(f"[webhook] Attempt {attempt} failed: {exc}")
            if attempt < retries:
                time.sleep(2 ** attempt)  # 2s, 4s, …

    print("[webhook] All attempts failed.")


# ---------------------------------------------------------------------------
# Core automation
# ---------------------------------------------------------------------------

def run(
    prompt: str,
    webhook_url: str,
    cookies_input: str,
    action: str = "text_to_video",
    image_url: str | None = None,
    aspect_ratio: str = "1:1",
    job_id: str | None = None,
) -> None:
    # Normalise optional args that may arrive as empty strings from CLI
    action = action.strip() or "text_to_video"
    aspect_ratio = aspect_ratio.strip() or "1:1"
    image_url = image_url.strip() if image_url else None

    with sync_playwright() as p:
        print("[browser] Launching Chromium (headless)…")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # --- Cookies ---
        print("[cookies] Parsing…")
        cookies = parse_netscape_cookies(cookies_input)
        if not cookies:
            print("[cookies] ERROR: No cookies parsed — aborting.")
            send_to_webhook(
                webhook_url, [], prompt, action, False,
                "No cookies parsed", job_id=job_id
            )
            browser.close()
            return
        context.add_cookies(cookies)
        print(f"[cookies] Loaded {len(cookies)} cookies.")

        page = context.new_page()
        temp_img_path = "temp_upload_img.jpg"

        try:
            # ----------------------------------------------------------------
            # Navigation — wait until the composer textarea is actually ready
            # ----------------------------------------------------------------
            print("[nav] Navigating to https://meta.ai/create …")
            page.goto("https://meta.ai/create", timeout=60_000)

            composer = page.locator('textarea[data-testid="composer-input"]').first
            try:
                composer.wait_for(state="attached", timeout=20_000)
            except PWTimeout:
                raise RuntimeError(
                    "Composer input never appeared — likely not logged in."
                )

            # ----------------------------------------------------------------
            # Capture baseline element sets AFTER page is ready
            # ----------------------------------------------------------------
            initial_video_srcs: set[str] = {
                v.get_attribute("src") or ""
                for v in page.locator("video").all()
                if v.get_attribute("src")
            }
            initial_image_srcs: set[str] = {
                img.get_attribute("src") or ""
                for img in page.locator('img[src^="https://scontent"]').all()
                if img.get_attribute("src")
            }
            print(
                f"[baseline] {len(initial_video_srcs)} videos, "
                f"{len(initial_image_srcs)} images before generation."
            )

            # ----------------------------------------------------------------
            # Mode selection (Image / Video)
            # ----------------------------------------------------------------
            is_video_mode = action in ("text_to_video",)

            try:
                placeholder: str = composer.get_attribute("placeholder") or ""
                current_is_video = "animation" in placeholder.lower()
            except Exception:
                current_is_video = False

            if is_video_mode != current_is_video:
                target_mode = "Video" if is_video_mode else "Image"
                print(f"[mode] Switching to {target_mode}…")
                try:
                    # Click the mode toggle button
                    mode_btn = page.locator(
                        'button[aria-label*="mode"], button:has-text("Image"), button:has-text("Video")'
                    ).last
                    mode_btn.click(timeout=5_000)

                    # Try role first, fall back to text match
                    menu_item = None
                    try:
                        item = page.get_by_role("menuitem", name=target_mode).first
                        item.wait_for(state="visible", timeout=4_000)
                        menu_item = item
                    except PWTimeout:
                        item = page.get_by_text(target_mode, exact=True).last
                        item.wait_for(state="visible", timeout=4_000)
                        menu_item = item

                    menu_item.click(timeout=5_000)
                    print(f"[mode] Switched to {target_mode}.")
                except Exception as exc:
                    print(f"[mode] Could not switch mode: {exc} — proceeding anyway.")

            # ----------------------------------------------------------------
            # Aspect ratio
            # ----------------------------------------------------------------
            if aspect_ratio and aspect_ratio != "1:1":
                print(f"[aspect] Setting aspect ratio → {aspect_ratio}…")
                try:
                    ratio_btn = page.locator(
                        'button, div[role="button"]'
                    ).filter(has_text=re.compile(r"1:1|9:16|16:9")).last
                    if ratio_btn.count() > 0:
                        ratio_btn.click(force=True, timeout=5_000)
                        target = page.get_by_text(aspect_ratio, exact=True).last
                        target.wait_for(state="visible", timeout=3_000)
                        target.click(force=True, timeout=5_000)
                    else:
                        print("[aspect] Dropdown not found — skipping.")
                except Exception as exc:
                    print(f"[aspect] Could not set aspect ratio: {exc}")

            # ----------------------------------------------------------------
            # Image upload (image_to_video)
            # ----------------------------------------------------------------
            if action == "image_to_video":
                if not image_url:
                    raise ValueError("--image-url is required for image_to_video")

                if image_url.startswith("http"):
                    print(f"[upload] Downloading image from {image_url}…")
                    img_data = requests.get(image_url, timeout=30).content
                    with open(temp_img_path, "wb") as f:
                        f.write(img_data)
                    upload_path = temp_img_path
                else:
                    upload_path = image_url

                print("[upload] Setting file input…")
                file_input = page.locator('input[type="file"]').first
                file_input.wait_for(state="attached", timeout=10_000)
                file_input.set_input_files(upload_path)

                # Wait for upload indicator rather than a fixed sleep
                try:
                    page.wait_for_selector(
                        '[data-testid="upload-preview"], img[alt*="upload"]',
                        timeout=15_000,
                    )
                    print("[upload] Upload confirmed.")
                except PWTimeout:
                    print("[upload] Upload indicator not found — continuing anyway.")

            # ----------------------------------------------------------------
            # Type prompt & submit
            # ----------------------------------------------------------------
            print(f"[prompt] Typing: {prompt!r}")
            composer.click(force=True)
            page.keyboard.type(prompt, delay=10)
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")
            print("[prompt] Submitted.")

            # ----------------------------------------------------------------
            # Wait for results
            # ----------------------------------------------------------------
            if action == "text_to_image":
                _wait_for_images(page, initial_image_srcs, webhook_url, prompt, action, job_id)

            elif action in ("animate_generation", "image_to_video", "text_to_video"):
                _wait_for_video(page, initial_video_srcs, initial_image_srcs, webhook_url, prompt, action, job_id)

            else:
                raise ValueError(f"Unknown action: {action!r}")

        except Exception as exc:
            print(f"[error] {exc}")
            try:
                page.screenshot(path="error_screenshot.png")
                print("[error] Screenshot saved → error_screenshot.png")
            except Exception:
                pass
            send_to_webhook(webhook_url, [], prompt, action, False, str(exc), job_id=job_id)

        finally:
            print("[browser] Closing.")
            browser.close()
            if os.path.exists(temp_img_path):
                os.remove(temp_img_path)
                print(f"[cleanup] Removed {temp_img_path}")


# ---------------------------------------------------------------------------
# Result-waiting helpers (extracted for clarity)
# ---------------------------------------------------------------------------

def _new_srcs(current: list[str], baseline: set[str]) -> list[str]:
    """Return URLs that are genuinely new (not in baseline)."""
    return [u for u in current if u and u not in baseline]


def _wait_for_images(
    page, initial_srcs: set[str], webhook_url, prompt, action, job_id
) -> None:
    print("[images] Waiting for new images…")
    found = False
    for _ in range(60):
        page.wait_for_timeout(3_000)
        current = [
            img.get_attribute("src") or ""
            for img in page.locator('img[src^="https://scontent"]').all()
        ]
        new_urls = _new_srcs(current, initial_srcs)
        if new_urls:
            found = True
            break

    if not found:
        print("[images] Timeout — collecting whatever is available.")
        current = [
            img.get_attribute("src") or ""
            for img in page.locator('img[src^="https://scontent"]').all()
        ]
        new_urls = _new_srcs(current, initial_srcs)

    new_urls = new_urls[:4]  # cap at 4
    if new_urls:
        print(f"[images] Found {len(new_urls)} new image(s).")
        send_to_webhook(webhook_url, new_urls, prompt, action, True, job_id=job_id)
    else:
        raise RuntimeError("No new image URLs found after generation.")


def _wait_for_video(
    page, initial_video_srcs: set[str], initial_image_srcs: set[str],
    webhook_url, prompt, action, job_id
) -> None:
    print("[video] Waiting for generation…")
    found_video = False
    clicked_animate = False

    for _ in range(120):
        page.wait_for_timeout(3_000)

        # Check for direct video appearance
        current_videos = [
            v.get_attribute("src") or ""
            for v in page.locator("video").all()
        ]
        new_vids = _new_srcs(current_videos, initial_video_srcs)
        if new_vids:
            print("[video] New video detected!")
            found_video = True
            break

        # Check for Animate button on a new image
        if not clicked_animate:
            current_imgs = [
                img.get_attribute("src") or ""
                for img in page.locator('img[src^="https://scontent"]').all()
            ]
            if _new_srcs(current_imgs, initial_image_srcs):
                animate_btn = page.locator(
                    'button[aria-label*="Animate"], button:has-text("Animate")'
                ).last
                if animate_btn.count() == 0:
                    # Try hovering last new image to reveal button
                    try:
                        new_img_els = [
                            img for img in page.locator('img[src^="https://scontent"]').all()
                            if img.get_attribute("src") not in initial_image_srcs
                        ]
                        if new_img_els:
                            new_img_els[-1].hover(force=True)
                            page.wait_for_timeout(800)
                            animate_btn = page.locator(
                                'button[aria-label*="Animate"], button:has-text("Animate")'
                            ).last
                    except Exception:
                        pass

                if animate_btn.count() > 0:
                    try:
                        animate_btn.click(force=True, timeout=5_000)
                        clicked_animate = True
                        print("[video] Clicked Animate — waiting for video…")
                    except Exception as exc:
                        print(f"[video] Failed to click Animate: {exc}")

    if not found_video:
        print("[video] Timeout — collecting whatever is available.")

    page.wait_for_timeout(2_000)
    current_videos = [
        v.get_attribute("src") or ""
        for v in page.locator("video").all()
    ]
    new_vids = _new_srcs(current_videos, initial_video_srcs)[:4]

    if new_vids:
        print(f"[video] Found {len(new_vids)} new video(s).")
        send_to_webhook(webhook_url, new_vids, prompt, action, True, job_id=job_id)
    else:
        raise RuntimeError("No new video URLs found after generation.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Meta AI Video/Image Generation Automation"
    )
    parser.add_argument("--prompt",       required=True,  help="Generation prompt")
    parser.add_argument("--webhook",      required=True,  help="Webhook URL for result")
    parser.add_argument("--cookies",      required=True,  help="Cookies file path or raw string")
    parser.add_argument("--job-id",       required=False, default=None)
    parser.add_argument("--action",       required=False, default="text_to_video")
    parser.add_argument("--image-url",    required=False, default=None)
    parser.add_argument("--aspect-ratio", required=False, default="1:1")

    args = parser.parse_args()
    run(
        prompt=args.prompt,
        webhook_url=args.webhook,
        cookies_input=args.cookies,
        action=args.action,
        image_url=args.image_url,
        aspect_ratio=args.aspect_ratio,
        job_id=args.job_id,
    )
