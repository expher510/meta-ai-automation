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
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
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
            time.sleep(5) # Wait for UI to stabilize
        except Exception as e:
            print(f"Failed to navigate: {e}")
            send_to_webhook(webhook_url, [], prompt, action, False, str(e), job_id=job_id)
            browser.close()
            return
            
        try:
            print("Looking for the chat input box...")
            
            # Mode selection
            is_video_mode = action in ["text_to_video"]
            
            # Wait for UI to settle
            time.sleep(3)
            
            # Check current placeholder to see if we need to switch
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
                    time.sleep(2)
                    # Try several ways to find the menu item
                    target = "Video" if is_video_mode else "Image"
                    menu_item = page.get_by_text(target, exact=True).last
                    if menu_item.count() == 0:
                        menu_item = page.get_by_role("menuitem", name=target).first
                    
                    menu_item.click(timeout=10000)
                    print(f"Successfully clicked {target} mode.")
                    time.sleep(2)
                except Exception as e:
                    print(f"Failed to switch mode via dropdown: {e}. Trying to proceed anyway...")

            # Grab textbox using data-testid which is more reliable
            chat_input = page.locator('textarea[data-testid="composer-input"]').first
            chat_input.wait_for(state="attached", timeout=15000)
            
            # Sometimes it's hidden but interactive, or becomes visible after a click
            try:
                chat_input.click(force=True)
            except:
                pass

            # Aspect ratio selection (only in image mode usually)
            if not is_video_mode and aspect_ratio and aspect_ratio != "1:1":
                print(f"Setting aspect ratio to {aspect_ratio}...")
                try:
                    # Find any button that looks like an aspect ratio (1:1, 9:16, 16:9)
                    ratio_btn = page.locator('button, div[role="button"]').filter(has_text=re.compile(r"1:1|9:16|16:9")).last
                    if ratio_btn.count() > 0:
                        ratio_btn.click(timeout=5000)
                        time.sleep(1)
                        page.get_by_text(aspect_ratio, exact=True).last.click(timeout=5000)
                        time.sleep(1)
                    else:
                        print("Aspect ratio dropdown not found. It might be unavailable.")
                except Exception as ratio_e:
                    print(f"Could not set aspect ratio: {ratio_e}")

            # Handle Image Upload if needed
            if action == "image_to_video":
                if not image_url:
                    raise Exception("image_url must be provided for image_to_video action")
                print(f"Downloading image from {image_url}...")
                img_data = requests.get(image_url).content
                with open("temp_upload_img.jpg", "wb") as f:
                    f.write(img_data)
                print("Uploading image...")
                file_input = page.locator('input[type="file"]').first
                if not file_input:
                    raise Exception("Could not find file input element")
                file_input.set_input_files("temp_upload_img.jpg")
                time.sleep(3) # wait for upload
            
            print(f"Typing prompt: {prompt}")
            try:
                chat_input.fill(prompt, force=True)
            except Exception as e:
                print(f"Fill failed, trying keyboard: {e}")
                chat_input.click(force=True)
                page.keyboard.type(prompt)
            
            page.keyboard.press("Enter")
            
            print(f"Prompt submitted. Executing action: {action}")
            
            if action == "text_to_image":
                print("Waiting for images to generate...")
                page.wait_for_selector('img[src^="https://scontent"]', timeout=180000)
                time.sleep(5)
                imgs = page.locator('img[src^="https://scontent"]').all()
                img_urls = [img.get_attribute("src") for img in imgs if img.get_attribute("src")]
                # Limit to latest 4 images to prevent payload bloat from history gallery
                img_urls = img_urls[:4]
                
                if img_urls:
                    print(f"Success! Found {len(img_urls)} new image(s)")
                    send_to_webhook(webhook_url, img_urls, prompt, action, True, job_id=job_id)
                else:
                    raise Exception("No image URLs found")
                    
            elif action == "animate_generation":
                print("Waiting for image to generate before animating...")
                page.wait_for_selector('img[src^="https://scontent"]', timeout=180000)
                time.sleep(10) # Wait for image to settle and buttons to appear
                
                print("Looking for 'Animate' button...")
                # Try bottom bar Animate button first
                animate_btn = page.locator('button:has-text("Animate")').last
                
                if animate_btn.count() == 0:
                    print("Bottom Animate button not found, trying image hover...")
                    imgs = page.locator('img[src^="https://scontent"]').all()
                    if imgs:
                        imgs[-1].hover()
                        time.sleep(1)
                        animate_btn = page.locator('button:has-text("Animate")').last
                        
                if animate_btn.count() > 0:
                    print(f"Clicking Animate button (found {animate_btn.count()} matches)...")
                    animate_btn.click(force=True)
                    print("Clicked Animate! Waiting for video...")
                else:
                    print("Could not find Animate button, taking debug screenshot...")
                    page.screenshot(path="animate_btn_not_found.png")
                    raise Exception("Could not find Animate button")
                
                page.wait_for_selector('video', timeout=180000)
                time.sleep(10)
                video_elements = page.locator('video').all()
                video_urls = [v.get_attribute("src") for v in video_elements if v.get_attribute("src")]
                # Limit to latest 4 videos
                video_urls = video_urls[:4]
                
                if video_urls:
                    print(f"Success! Found {len(video_urls)} new video(s)")
                    send_to_webhook(webhook_url, video_urls, prompt, action, True, job_id=job_id)
                else:
                    raise Exception("No video URLs found after animating")

            else: # text_to_video or image_to_video
                print("Waiting for videos to generate...")
                try:
                    page.wait_for_selector('video', timeout=180000)
                    print("First video detected. Waiting for all 4 videos...")
                    time.sleep(10)
                    
                    video_elements = page.locator('video').all()
                    video_urls = []
                    for video in video_elements:
                        src = video.get_attribute("src")
                        if src:
                            video_urls.append(src)
                    
                    # Limit to latest 4 videos
                    video_urls = video_urls[:4]
                    print(f"Total new videos found: {len(video_urls)}")
                    
                    if video_urls:
                        send_to_webhook(webhook_url, video_urls, prompt, action, True, job_id=job_id)
                        return
                except Exception as inner_e:
                    print(f"Video element didn't appear naturally. Checking for 'Animate' button fallback...")
                
                try:
                    animate_btn = page.get_by_role("button", name="Animate").first
                    if animate_btn.count() > 0:
                        animate_btn.click()
                        print("Clicked Animate fallback. Waiting for video...")
                        page.wait_for_selector('video', timeout=180000)
                        time.sleep(10)
                        video_elements = page.locator('video').all()
                        video_urls = [v.get_attribute("src") for v in video_elements if v.get_attribute("src")][:4]
                        if video_urls:
                            send_to_webhook(webhook_url, video_urls, prompt, action, True, job_id=job_id)
                            return
                except:
                    pass
                    
                print("No video URLs found.")
                try:
                    page.screenshot(path="error_screenshot.png")
                except:
                    pass
                send_to_webhook(webhook_url, [], prompt, action, False, "No video URLs found", job_id=job_id)
                
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
    parser.add_argument("--action", required=False, default="text_to_video", choices=["text_to_video", "text_to_image", "animate_generation", "image_to_video"], help="Type of action to perform")
    parser.add_argument("--image-url", required=False, default=None, help="URL of the image to upload for image_to_video action")
    parser.add_argument("--aspect-ratio", required=False, default="1:1", choices=["1:1", "9:16", "16:9"], help="Aspect ratio for image generation")
    
    args = parser.parse_args()
    run(args.prompt, args.webhook, args.cookies, args.action, args.image_url, args.aspect_ratio, args.job_id)
