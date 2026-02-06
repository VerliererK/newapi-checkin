import os
import json
import asyncio
import logging
import argparse
from utils.exceptions import HTTPError
from utils.notify import create_notifiers, send_notifications
from linuxdo import login_linuxdo, oauth_authorize, STATE_FILE

from playwright.async_api import async_playwright

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)

CONFIG_FILE = 'config.json'
COOKIES_CACHE = 'cookies_cache.json'
QUOTA_DIVISOR = 500000
DEFAULT_ENDPOINT = '/api/user/sign_in'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'


def load_config():
    """Load config from environment variables or config.json"""
    linuxdo_email = os.environ.get('LINUXDO_EMAIL')
    linuxdo_password = os.environ.get('LINUXDO_PASSWORD')
    accounts_env = os.environ.get('CHECKIN_ACCOUNTS')
    notify_env = os.environ.get('CHECKIN_NOTIFY')

    if accounts_env:
        logging.info('Loading config from environment variables')
        config = {
            'accounts': json.loads(accounts_env),
            'notifications': json.loads(notify_env) if notify_env else [],
        }
    elif os.path.exists(CONFIG_FILE):
        logging.info('Loading config from config.json')
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        raise RuntimeError('No config found. Set CHECKIN_ACCOUNTS env or create config.json')

    # LinuxDo credentials always from environment variables
    if linuxdo_email and linuxdo_password:
        config['linuxdo'] = {'email': linuxdo_email, 'password': linuxdo_password}
    else:
        config.pop('linuxdo', None)

    return config


def load_cookies_cache():
    """Load cached cookies from file."""
    if os.path.exists(COOKIES_CACHE):
        try:
            with open(COOKIES_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f'Failed to load cookies cache: {e}')
    return {}


def save_cookies_cache(cache):
    """Save cookies cache to file."""
    with open(COOKIES_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)
    logging.info(f'Cookies cache saved to {COOKIES_CACHE}')


def update_cookies_cache(cache, domain, api_user, cookies):
    """Update a single account's cookies in cache."""
    cache[domain] = {'api_user': api_user, 'cookies': cookies}


async def get_quota(page, domain, api_user):
    url = f'{domain}/api/user/self'
    await page.set_extra_http_headers({'new-api-user': api_user})
    response = await page.goto(url)
    await page.wait_for_load_state('networkidle')
    text = await response.text() if response else ''

    if not response:
        raise HTTPError('[/api/user/self] No response', status_code=0)
    if not response.ok:
        raise HTTPError(f'[/api/user/self] HTTP {response.status}: {text[:100]}', status_code=response.status)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPError(f'[/api/user/self] Invalid JSON response: {e}', status_code=response.status)

    if not data.get('success'):
        raise HTTPError(f'[/api/user/self] Failed to get user info: {data}', status_code=response.status)

    user_data = data.get('data', {})
    quota = round(user_data.get('quota', 0) / QUOTA_DIVISOR, 2)
    used_quota = round(user_data.get('used_quota', 0) / QUOTA_DIVISOR, 2)
    logging.info(f'Balance: ${quota}, Used: ${used_quota}')
    return quota


async def do_checkin(page, domain, api_user, endpoint=DEFAULT_ENDPOINT):
    url = f'{domain}{endpoint}'
    await page.set_extra_http_headers({'new-api-user': api_user})
    response = await page.evaluate(f"fetch('{url}', {{method: 'POST'}}).then(r => r.text())")
    await page.wait_for_load_state('networkidle')
    logging.info(f'Checkin result: {response}')


async def _create_fallback_context(browser, domain, cookies_dict):
    """Create a new browser context with cached cookies for fallback."""
    bare_domain = domain.replace('https://', '').replace('http://', '')
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={'width': 800, 'height': 600},
        ignore_https_errors=True,
    )
    await context.add_cookies([{'name': k, 'value': v, 'domain': bare_domain, 'path': '/'} for k, v in cookies_dict.items()])
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    return context, page


async def process_account(browser, linuxdo_context, account, notifiers, cookies_cache):
    name = account.get('name', 'Unknown')
    domain = account.get('domain', '')
    endpoint = account.get('endpoint', DEFAULT_ENDPOINT)

    if not domain:
        logging.warning(f'[{name}] Missing domain, skipping')
        return

    try:
        # Step 1: Try checkin with cached cookies first
        cached = cookies_cache.get(domain)
        if cached:
            api_user = cached['api_user']
            cookies_dict = cached['cookies']
            logging.info(f'[{name}] Using cached cookies (api_user={api_user})')

            fallback_context, page = await _create_fallback_context(browser, domain, cookies_dict)
            try:
                await page.goto(domain)
                await page.wait_for_load_state('networkidle')
                await _run_checkin(page, name, domain, api_user, endpoint, notifiers)
                return  # Success, no need for OAuth
            except Exception as e:
                logging.warning(f'[{name}] Checkin with cached cookies failed: {e}')
            finally:
                await fallback_context.close()

        # Step 2: Cache miss or checkin failed, try OAuth
        if not linuxdo_context:
            if cached:
                logging.error(f'[{name}] Checkin failed and OAuth unavailable, skipping')
            else:
                logging.error(f'[{name}] No cached cookies and OAuth unavailable, skipping')
            return

        logging.info(f'[{name}] Attempting OAuth to get fresh cookies...')
        page = await linuxdo_context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        try:
            api_user, cookies_dict = await oauth_authorize(linuxdo_context, page, account)
            if api_user and cookies_dict:
                update_cookies_cache(cookies_cache, domain, api_user, cookies_dict)
                logging.info(f'[{name}] OAuth cookies obtained and cached')
                await _run_checkin(page, name, domain, api_user, endpoint, notifiers)
            else:
                logging.error(f'[{name}] OAuth returned empty cookies')
        finally:
            await page.close()

    except HTTPError as e:
        err_msg = f'[{name}] HTTP error: {e}'
        logging.error(err_msg)
        send_notifications(notifiers, '!! NewAPI Checkin Error !!', err_msg)
    except Exception as e:
        err_msg = f'[{name}] Execution error: {e}'
        logging.error(err_msg)
        send_notifications(notifiers, '!! NewAPI Checkin Error !!', err_msg)


async def _run_checkin(page, name, domain, api_user, endpoint, notifiers):
    """Execute the checkin flow: get_quota -> do_checkin -> get_quota."""
    logging.info(f'[{name}] Accessing: {domain}')
    old_quota = await get_quota(page, domain, api_user)
    await do_checkin(page, domain, api_user, endpoint)
    new_quota = await get_quota(page, domain, api_user)

    if new_quota > old_quota:
        msg = f'[{name}] Checkin success, Quota: {old_quota} -> {new_quota}'
        logging.info(msg)
        send_notifications(notifiers, '!! NewAPI Checkin Success !!', msg)


async def main():
    config = load_config()
    parser = argparse.ArgumentParser(description='Checkin')
    parser.add_argument('--channel', type=str, default='chromium', help='Browser channel')
    args = parser.parse_args()

    notifiers = create_notifiers(config.get('notifications', []))
    cookies_cache = load_cookies_cache()
    linuxdo_config = config.get('linuxdo', {})
    accounts = config.get('accounts', [])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel=args.channel)
        linuxdo_context = None

        try:
            # Login to LinuxDo if config provided
            if linuxdo_config.get('email') and linuxdo_config.get('password'):
                try:
                    linuxdo_context = await login_linuxdo(
                        browser,
                        linuxdo_config['email'],
                        linuxdo_config['password'],
                        notifiers,
                    )
                except Exception as e:
                    logging.error(f'LinuxDo login failed: {e}, will use cached cookies')

            # Process each account
            for account in accounts:
                await process_account(browser, linuxdo_context, account, notifiers, cookies_cache)

            # Save cookies cache
            save_cookies_cache(cookies_cache)

            # Save linuxdo state if context exists
            if linuxdo_context:
                try:
                    await linuxdo_context.storage_state(path=STATE_FILE)
                    logging.info(f'LinuxDo state saved to {STATE_FILE}')
                except Exception as e:
                    logging.warning(f'Failed to save LinuxDo state: {e}')

        except Exception as e:
            logging.error(f'Execution error: {e}')
        finally:
            if linuxdo_context:
                await linuxdo_context.close()
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
