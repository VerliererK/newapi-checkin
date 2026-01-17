import os
import json
import asyncio
import logging
import argparse
from utils.exceptions import HTTPError

from playwright.async_api import async_playwright

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)

CONFIG_ENV = "CHECKIN_CONFIG"
CONFIG_FILE = "config.json"
QUOTA_DIVISOR = 500000
DEFAULT_ENDPOINT = "/api/user/sign_in"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"


def load_config():
    """Load config from environment variable or config.json"""
    env_config = os.environ.get(CONFIG_ENV)
    if env_config:
        logging.info("Loading config from environment variable")
        return json.loads(env_config)

    if os.path.exists(CONFIG_FILE):
        logging.info("Loading config from config.json")
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError("No config found. Set CHECKIN_CONFIG env or create config.json")


def parse_cookies(cookies):
    """Parse cookies from string or dict format"""
    if isinstance(cookies, dict):
        return cookies
    if isinstance(cookies, str):
        result = {}
        for item in cookies.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                result[key.strip()] = value.strip()
        return result
    return {}


def parse_domain(domain):
    return domain.replace("https://", "").replace("http://", "")


async def get_quota(page, domain, api_user):
    url = f"{domain}/api/user/self"
    await page.set_extra_http_headers({"new-api-user": api_user})
    response = await page.goto(url)
    await page.wait_for_load_state("networkidle")
    text = await response.text() if response else ""

    if not response:
        raise HTTPError("[/api/user/self] No response", status_code=0)
    if not response.ok:
        raise HTTPError(f"[/api/user/self] HTTP {response.status}: {text[:100]}", status_code=response.status)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPError(f"[/api/user/self] Invalid JSON response: {e}", status_code=response.status)

    if not data.get("success"):
        raise HTTPError(f"[/api/user/self] Failed to get user info: {data}", status_code=response.status)

    user_data = data.get("data", {})
    quota = round(user_data.get("quota", 0) / QUOTA_DIVISOR, 2)
    used_quota = round(user_data.get("used_quota", 0) / QUOTA_DIVISOR, 2)
    logging.info(f"Balance: ${quota}, Used: ${used_quota}")
    return quota


async def checkin(page, domain, api_user, endpoint=DEFAULT_ENDPOINT):
    url = f"{domain}{endpoint}"
    await page.set_extra_http_headers({"new-api-user": api_user})
    response = await page.evaluate(f"fetch('{url}', {{method: 'POST'}}).then(r => r.text())")
    await page.wait_for_load_state("networkidle")
    logging.info(f"Checkin result: {response}")


async def create_context(browser, domain, cookies):
    cookie_dict = parse_cookies(cookies)
    context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 800, "height": 600}, ignore_https_errors=True)
    await context.add_cookies([{"name": k, "value": v, "domain": parse_domain(domain), "path": "/"} for k, v in cookie_dict.items()])
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    return context, page


async def process_account(browser, account):
    name = account.get("name", "Unknown")
    domain = account.get("domain", "")
    endpoint = account.get("endpoint", DEFAULT_ENDPOINT)
    cookies = account.get("cookies", {})
    api_user = account.get("api_user", "")

    if not domain or not api_user or not cookies:
        logging.warning(f"[{name}] Missing domain, api_user or cookies, skipping")
        return

    context, page = await create_context(browser, domain, cookies)
    try:
        logging.info(f"[{name}] Accessing: {domain}")
        await page.goto(domain)
        await page.wait_for_load_state("networkidle")

        await get_quota(page, domain, api_user)
        await checkin(page, domain, api_user, endpoint)
        await get_quota(page, domain, api_user)
    except HTTPError as e:
        logging.error(f"[{name}] HTTP error: {e}")
    except Exception as e:
        logging.error(f"[{name}] Execution error: {e}")
    finally:
        await context.close()


async def main():
    config = load_config()
    parser = argparse.ArgumentParser(description="Checkin")
    parser.add_argument('--channel', type=str, default='chromium', help='Browser channel')
    args = parser.parse_args()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel=args.channel)
        try:
            for account in config:
                await process_account(browser, account)
        except Exception as e:
            logging.error(f"Execution error: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
