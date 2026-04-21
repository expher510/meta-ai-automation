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
                    # Some cookies have very large expiration dates which might fail in Playwright
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
            # Meta AI uses a contenteditable div with role="textbox"
            chat_input = page.get_by_role("textbox").first
            chat_input.wait_for(state="visible", timeout=15000)
            
            print(f"Typing prompt: {prompt}")
            chat_input.click()
            page.keyboard.type(prompt)
            page.keyboard.press("Enter")
            
            print("Prompt submitted. Waiting for generation to complete...")
            # Videos usually take some time. We wait for a video element to appear.
            # Meta AI might show a loading state first.
            
            # This logic waits for any <video> tag that gets added to the page.
            # If the video is inside a specific container, update the selector.
            video_locator = page.locator('video').last
            
            # We give it up to 3 minutes to generate
            video_locator.wait_for(state="attached", timeout=180000)
            
            # Wait a few seconds for the src to fully populate
            time.sleep(5)
            
            video_url = video_locator.get_attribute("src")
            if video_url:
                print(f"Success! Generated Video URL: {video_url}")
                send_to_webhook(webhook_url, [video_url], prompt, True, job_id=job_id)
            else:
                print("Video element found, but could not extract the 'src' attribute.")
                send_to_webhook(webhook_url, [], prompt, False, "Video element found but no src attribute", job_id=job_id)
                
        except Exception as e:
            print(f"Error during automation: {e}")
            # Try to grab a screenshot for debugging
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
