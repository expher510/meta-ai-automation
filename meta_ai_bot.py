import argparse
import time
import requests
import sys
from playwright.sync_api import sync_playwright

def parse_netscape_cookies(file_path_or_content):
    """
    Parses cookies from Netscape HTTP Cookie File format into Playwright format.
    Supports reading from a file path or direct string content.
    """
    cookies = []
    
    # Check if it's a file path or raw content
    try:
        with open(file_path_or_content, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        content = file_path_or_content

    # Try parsing as JSON first
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

def run(prompt, webhook_url, cookies_input, job_id=None):
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Anti-detection
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
        """)
        
        # Parse and load cookies
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
            send_to_webhook(webhook_url, [], prompt, False, str(e), job_id=job_id)
            browser.close()
            return
            
        try:
            print("Looking for the chat input box...")
            chat_input = page.get_by_role("textbox").first
            chat_input.wait_for(state="visible", timeout=15000)
            
            print(f"Typing prompt: {prompt}")
            chat_input.click()
            page.keyboard.type(prompt)
            page.keyboard.press("Enter")
            
            print("Prompt submitted. Waiting for videos to generate...")
            
            # Wait for the first video to appear
            page.wait_for_selector('video', timeout=180000)
            
            # Wait a bit for all 4 videos to complete
            print("First video detected. Waiting for all 4 videos...")
            time.sleep(10)
            
            # Collect all videos
            video_elements = page.locator('video').all()
            video_urls = []
            for i, video in enumerate(video_elements):
                src = video.get_attribute("src")
                if src:
                    video_urls.append(src)
                    print(f"Video {i+1}: {src[:80]}...")
                    
            print(f"Total videos found: {len(video_urls)}")
            
            if video_urls:
                send_to_webhook(webhook_url, video_urls, prompt, True, job_id=job_id)
            else:
                print("No video URLs found.")
                try:
                    page.screenshot(path="error_screenshot.png")
                except:
                    pass
                send_to_webhook(webhook_url, [], prompt, False, "No video URLs found", job_id=job_id)
                
        except Exception as e:
            print(f"Error during automation: {e}")
            try:
                page.screenshot(path="error_screenshot.png")
                print("Saved error screenshot to error_screenshot.png")
            except:
                pass
            send_to_webhook(webhook_url, [], prompt, False, str(e), job_id=job_id)
            
        finally:
            print("Closing browser...")
            browser.close()

def send_to_webhook(webhook_url, video_urls, prompt, success=True, error=None, job_id=None):
    if not webhook_url:
        print("No webhook URL provided. Skipping webhook.")
        return
        
    payload = {
        "job_id": job_id,
        "success": success,
        "prompt": prompt,
        "video_urls": video_urls,
        "video_count": len(video_urls) if video_urls else 0,
        "error": error
    }
    
    print(f"Sending webhook to {webhook_url}...")
    try:
        response = requests.post(webhook_url, json=payload, timeout=30)
        response.raise_for_status()
        print(f"Successfully sent result to webhook. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send webhook: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Meta AI Video Generation Automation")
    parser.add_argument("--prompt", required=True, help="Prompt to generate video")
    parser.add_argument("--webhook", required=True, help="Webhook URL to send the result")
    parser.add_argument("--cookies", required=True, help="Path to the Netscape format cookies file OR the cookie string itself")
    parser.add_argument("--job-id", required=False, default=None, help="Job ID to return with the result")
    
    args = parser.parse_args()
    run(args.prompt, args.webhook, args.cookies, args.job_id)