import argparse
import time
import requests
import sys
import os
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
        # Fallback for old webhooks expecting video_urls even for images
        payload["video_urls"] = media_urls
        payload["video_count"] = len(media_urls) if media_urls else 0
    
    print(f"Sending webhook to {webhook_url}...")
    try:
        response = requests.post(webhook_url, json=payload, timeout=30)
        response.raise_for_status()
        print(f"Successfully sent result to webhook. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send webhook: {e}")

def run(prompt, webhook_url, cookies_input, action="text_to_video", image_url=None, job_id=None):
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
        
        print("Navigating to https://meta.ai/ ...")
        try:
            page.goto("https://meta.ai/", timeout=60000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"Failed to navigate: {e}")
            send_to_webhook(webhook_url, [], prompt, action, False, str(e), job_id=job_id)
            browser.close()
            return
            
        try:
            print("Looking for the chat input box...")
            chat_input = page.get_by_role("textbox").first
            chat_input.wait_for(state="visible", timeout=15000)

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
            
            # For text_to_video, ensure prompt explicitly requests a video to guarantee it generates one
            actual_prompt = prompt
            if action == "text_to_video":
                if not actual_prompt.lower().startswith("animate") and "video" not in actual_prompt.lower():
                    print("Auto-prefixing prompt with 'Generate an animated video of ' to ensure video creation...")
                    actual_prompt = f"Generate an animated video of {prompt}"

            print(f"Typing prompt: {actual_prompt}")
            chat_input.click()
            page.keyboard.type(actual_prompt)
            page.keyboard.press("Enter")
            
            print(f"Prompt submitted. Executing action: {action}")
            
            if action == "text_to_image":
                print("Waiting for images to generate...")
                page.wait_for_selector('img[src^="https://scontent"]', timeout=180000)
                time.sleep(5)
                imgs = page.locator('img[src^="https://scontent"]').all()
                img_urls = [img.get_attribute("src") for img in imgs if img.get_attribute("src")]
                if img_urls:
                    print(f"Success! Found {len(img_urls)} image(s)")
                    send_to_webhook(webhook_url, img_urls, prompt, action, True, job_id=job_id)
                else:
                    raise Exception("No image URLs found")
                    
            elif action == "animate_generation":
                print("Waiting for image to generate before animating...")
                page.wait_for_selector('img[src^="https://scontent"]', timeout=180000)
                time.sleep(5)
                
                print("Looking for 'Animate' button...")
                animate_btn = page.get_by_text("Animate", exact=False).first
                if animate_btn.count() == 0:
                    animate_btn = page.get_by_role("button", name="Animate").first
                if animate_btn.count() == 0:
                    imgs = page.locator('img[src^="https://scontent"]').all()
                    if imgs:
                        imgs[-1].click()
                        time.sleep(2)
                        animate_btn = page.get_by_role("button", name="Animate").first
                        
                if animate_btn.count() > 0:
                    animate_btn.click()
                    print("Clicked Animate! Waiting for video...")
                else:
                    raise Exception("Could not find Animate button")
                
                page.wait_for_selector('video', timeout=180000)
                time.sleep(10)
                video_elements = page.locator('video').all()
                video_urls = [v.get_attribute("src") for v in video_elements if v.get_attribute("src")]
                
                if video_urls:
                    print(f"Success! Found {len(video_urls)} video(s)")
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
                    for i, video in enumerate(video_elements):
                        src = video.get_attribute("src")
                        if src:
                            video_urls.append(src)
                            print(f"Video {i+1}: {src[:80]}...")
                            
                    print(f"Total videos found: {len(video_urls)}")
                    
                    if video_urls:
                        send_to_webhook(webhook_url, video_urls, prompt, action, True, job_id=job_id)
                        return
                except Exception as inner_e:
                    print(f"Video element didn't appear naturally. Checking for 'Animate' button fallback...")
                
                # Check if it generated an image instead, and if we should animate it
                try:
                    animate_btn = page.get_by_role("button", name="Animate").first
                    if animate_btn.count() > 0:
                        animate_btn.click()
                        print("Clicked Animate fallback. Waiting for video...")
                        page.wait_for_selector('video', timeout=180000)
                        time.sleep(10)
                        video_elements = page.locator('video').all()
                        video_urls = [v.get_attribute("src") for v in video_elements if v.get_attribute("src")]
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
    
    args = parser.parse_args()
    run(args.prompt, args.webhook, args.cookies, args.action, args.image_url, args.job_id)
