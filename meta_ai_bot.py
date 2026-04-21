import argparse
import time
import requests
import sys
import os
import re
from playwright.sync_api import sync_playwright

def parse_netscape_cookies(file_path_or_content):
    cookies = []
    try:
        with open(file_path_or_content, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        content = file_path_or_content
    try:
        import json
        cookies = json.loads(content)
        if isinstance(cookies, list):
            return cookies
    except Exception:
        pass
    lines = content.splitlines()
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        parts = line.strip().split('\t')
        if len(parts) >= 7:
            cookie = {
                'domain': parts[0],
                'path': parts[2],
                'secure': parts[3].lower() == 'true',
                'name': parts[5],
                'value': parts[6]
            }
            try:
                expires = float(parts[4])
                if expires > 0:
                    cookie['expires'] = expires
            except ValueError:
                pass
            cookies.append(cookie)
    return cookies

def send_to_webhook(webhook_url, media_urls, prompt, action_type, success=True, error=None, job_id=None):
    if not webhook_url:
        print("No webhook URL provided. Skipping webhook.")
        return
        
    payload = {
        "job_id": job_id,
        "success": success,
        "prompt": prompt,
        "media_type": "video" if action_type in ["text_to_video", "animate_generation", "image_to_video"] else "image",
        "error": error
    }
    
    if payload["media_type"] == "video":
        payload["video_urls"] = media_urls
        payload["video_count"] = len(media_urls) if media_urls else 0
    else:
        payload["image_urls"] = media_urls
        payload["image_count"] = len(media_urls) if media_urls else 0
        payload["video_urls"] = media_urls
        payload["video_count"] = len(media_urls) if media_urls else 0
    
    print(f"Sending webhook to {webhook_url}...")
    try:
        response = requests.post(webhook_url, json=payload, timeout=30)
        response.raise_for_status()
        print(f"Successfully sent result to webhook. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send webhook: {e}")

def run(prompt, webhook_url, cookies_input, action="text_to_video", image_url=None, aspect_ratio="1:1", job_id=None):
    if not action:
        action = "text_to_video"
    if not aspect_ratio:
        aspect_ratio = "1:1"
        
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        print("Parsing cookies...")
        cookies = parse_netscape_cookies(cookies_input)
        if cookies:
            context.add_cookies(cookies)
            print(f"Loaded {len(cookies)} cookies into the browser context.")
        else:
            print("WARNING: No cookies parsed. You might be asked to log in, which will fail the automation.")
            
        page = context.new_page()
        
        print("Navigating to https://meta.ai/create ...")
        try:
            page.goto("https://meta.ai/create", timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            # Wait dynamically instead of static sleep
        except Exception as e:
            print(f"Failed to navigate: {e}")
            send_to_webhook(webhook_url, [], prompt, action, False, str(e), job_id=job_id)
            browser.close()
            return
            
        try:
            print("Looking for the chat input box...")
            
            # Mode selection
            is_video_mode = action in ["text_to_video"]
            
            # Grab textbox using data-testid which is more reliable
            chat_input = page.locator('textarea[data-testid="composer-input"]').first
            chat_input.wait_for(state="attached", timeout=15000)
            placeholder_text = "Describe your animation" if is_video_mode else "Describe your image"
            
            # Find the mode toggle button (it usually shows 'Image' or 'Video')
            mode_button = page.locator('button:has-text("Image"), button:has-text("Video")').last
            
            current_mode = "Video" if "animation" in (page.get_by_role("textbox").first.get_attribute("placeholder") or "").lower() else "Image"
            # Alternative check if role textbox fails
            try:
                placeholder = page.locator('textarea[data-testid="composer-input"]').get_attribute("placeholder")
                current_mode = "Video" if "animation" in (placeholder or "").lower() else "Image"
            except:
                pass

            if (is_video_mode and current_mode == "Image") or (not is_video_mode and current_mode == "Video"):
                print(f"Switching mode to {'Video' if is_video_mode else 'Image'}...")
                try:
                    mode_button.click()
                    # Try several ways to find the menu item
                    target = "Video" if is_video_mode else "Image"
                    menu_item = page.get_by_text(target, exact=True).last
                    menu_item = page.get_by_role("menuitem", name=target).first
                    
                    menu_item.wait_for(state="visible", timeout=5000)
                    menu_item.click(timeout=10000)
                    print(f"Successfully clicked {target} mode.")
                except Exception as e:
                    print(f"Failed to switch mode via dropdown: {e}. Trying to proceed anyway...")

            # Sometimes it's hidden but interactive, or becomes visible after a click
            try:
                chat_input.click(force=True)
            except:
                pass

            # Aspect ratio selection
            if aspect_ratio and aspect_ratio != "1:1":
                print(f"Setting aspect ratio to {aspect_ratio}...")
                try:
                    # Find any button that looks like an aspect ratio (1:1, 9:16, 16:9)
                    ratio_btn = page.locator('button, div[role="button"]').filter(has_text=re.compile(r"1:1|9:16|16:9")).last
                    if ratio_btn.count() > 0:
                        ratio_btn.click(force=True, timeout=5000)
                        target_ratio = page.get_by_text(aspect_ratio, exact=True).last
                        target_ratio.wait_for(state="visible", timeout=3000)
                        target_ratio.click(force=True, timeout=5000)
                    else:
                        print("Aspect ratio dropdown not found. It might be unavailable.")
                except Exception as ratio_e:
                    print(f"Could not set aspect ratio: {ratio_e}")

            # Handle Image Upload if needed
            if action == "image_to_video":
                if not image_url:
                    raise Exception("image_url is required for image_to_video action")
                if image_url.startswith("http"):
                    print(f"Downloading image from {image_url}...")
                    img_data = requests.get(image_url).content
                    with open("temp_upload_img.jpg", "wb") as f:
                        f.write(img_data)
                    upload_path = "temp_upload_img.jpg"
                else:
                    upload_path = image_url
                    
                print("Uploading image...")
                file_input = page.locator('input[type="file"]').first
                if not file_input:
                    raise Exception("Could not find file input element")
                file_input.set_input_files(upload_path)
                time.sleep(5) # wait for upload, reduced from 8s
            
            # Count existing media to detect new generations
            initial_video_count = page.locator('video').count()
            initial_image_count = page.locator('img[src^="https://scontent"]').count()

            print(f"Typing prompt: {prompt}")
            try:
                chat_input.focus()
                chat_input.click(force=True)
                page.keyboard.type(prompt, delay=10) # human-like typing to trigger React events
                time.sleep(1)
            except Exception as e:
                print(f"Typing failed: {e}")
            
            page.keyboard.press("Enter")
            
            print(f"Prompt submitted. Executing action: {action}")
            
            if action == "text_to_image":
                print("Waiting for images to generate...")
                for _ in range(60):
                    time.sleep(3)
                    if page.locator('img[src^="https://scontent"]').count() > initial_image_count:
                        break
                else:
                    print("Timeout waiting for new image. Proceeding anyway...")
                    
                time.sleep(5)
                imgs = page.locator('img[src^="https://scontent"]').all()
                img_urls = [img.get_attribute("src") for img in imgs if img.get_attribute("src")]
                # Return the new images (difference in count, max 4)
                new_count = max(0, min(4, len(img_urls) - initial_image_count))
                img_urls = img_urls[:new_count]
                
                if img_urls:
                    print(f"Success! Found {len(img_urls)} new image(s)")
                    send_to_webhook(webhook_url, img_urls, prompt, action, True, job_id=job_id)
                else:
                    raise Exception("No image URLs found")
                    
            elif action in ["animate_generation", "image_to_video", "text_to_video"]:
                print("Waiting for generation to finish...")
                
                clicked_animate = False
                found_video = False
                
                for _ in range(120):
                    time.sleep(3)
                    
                    # 1. Did a video appear directly?
                    if page.locator('video').count() > initial_video_count:
                        print("New video detected!")
                        found_video = True
                        break
                        
                    # 2. Did an image appear that needs to be animated?
                    if not clicked_animate:
                        imgs = page.locator('img[src^="https://scontent"]').all()
                        if imgs and len(imgs) > initial_image_count:
                            animate_btn = page.locator('button:has-text("Animate")').last
                            
                            if animate_btn.count() == 0:
                                # Try hovering the latest image to reveal the Animate button
                                try:
                                    imgs[-1].hover(force=True)
                                    time.sleep(1)
                                    animate_btn = page.locator('button:has-text("Animate")').last
                                except:
                                    pass
                                    
                            if animate_btn.count() > 0:
                                print(f"Found Animate button on new image. Clicking it to generate video...")
                                try:
                                    animate_btn.click(force=True)
                                    clicked_animate = True
                                    print("Clicked Animate! Waiting for video...")
                                except Exception as click_e:
                                    print(f"Failed to click Animate: {click_e}")
                
                if not found_video:
                    print("Timeout waiting for video. Proceeding anyway...")
                    
                time.sleep(2) # Reduced from 5
                video_elements = page.locator('video').all()
                video_urls = [v.get_attribute("src") for v in video_elements if v.get_attribute("src")]
                
                new_count = max(0, min(4, len(video_urls) - initial_video_count))
                video_urls = video_urls[:new_count]
                
                if video_urls:
                    print(f"Success! Found {len(video_urls)} new video(s)")
                    send_to_webhook(webhook_url, video_urls, prompt, action, True, job_id=job_id)
                else:
                    raise Exception("No video URLs found after processing")
                
        except Exception as e:
            print(f"Error during automation: {e}")
            try:
                page.screenshot(path="error_screenshot.png")
                print("Saved error screenshot to error_screenshot.png")
            except:
                pass
            send_to_webhook(webhook_url, [], prompt, action, False, str(e), job_id=job_id)
            
        finally:
            print("Closing browser...")
            browser.close()
            if os.path.exists("temp_upload_img.jpg"):
                os.remove("temp_upload_img.jpg")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Meta AI Video/Image Generation Automation")
    parser.add_argument("--prompt", required=True, help="Prompt to generate")
    parser.add_argument("--webhook", required=True, help="Webhook URL to send the result")
    parser.add_argument("--cookies", required=True, help="Path to the Netscape format cookies file OR the cookie string itself")
    parser.add_argument("--job-id", required=False, default=None, help="Job ID to return with the result")
    parser.add_argument("--action", required=False, default="text_to_video", help="Type of action to perform")
    parser.add_argument("--image-url", required=False, default=None, help="URL of the image to upload for image_to_video action")
    parser.add_argument("--aspect-ratio", required=False, default="1:1", help="Aspect ratio for image generation")
    
    args = parser.parse_args()
    run(args.prompt, args.webhook, args.cookies, args.action, args.image_url, args.aspect_ratio, args.job_id)
