import asyncio
import os
import json
import logging

from utils.notify import send_notifications

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linuxdo_state.json')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
CONNECT_URL = 'https://connect.linux.do/oauth2/authorize'


async def _create_context_with_state(browser):
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={'width': 800, 'height': 600},
        ignore_https_errors=True,
        storage_state=STATE_FILE,
    )
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    return context, page


async def _is_logged_in(page):
    await page.goto('https://linux.do')
    try:
        await page.wait_for_selector('#current-user', timeout=5000)
        return True
    except:
        return False


async def login_linuxdo(browser, username, password, notifiers=None):
    domain = 'https://linux.do/login'

    if os.path.exists(STATE_FILE):
        logging.info('[linuxDo] Found saved state, attempting to restore session...')
        try:
            context, page = await _create_context_with_state(browser)
            if await _is_logged_in(page):
                logging.info('[linuxDo] Session restored from saved state')
                return context
            else:
                logging.info('[linuxDo] Saved state expired, will login again')
                await context.close()
        except Exception as e:
            logging.warning(f'[linuxDo] Failed to restore state: {e}')

    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={'width': 800, 'height': 600},
        ignore_https_errors=True,
    )
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

    try:
        logging.info(f'[linuxDo] Accessing: {domain}')
        await page.goto(domain)
        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except:
            pass

        if domain != page.url:
            logging.info('[linuxDo] Already logged in')
            await context.storage_state(path=STATE_FILE)
            return context

        logging.info('[linuxDo] Attempting login...')
        await page.wait_for_selector('#login-account-name')
        await page.fill('#login-account-name', username)
        await page.fill('#login-account-password', password)
        await page.click('#login-button')
        await page.wait_for_selector('#current-user', timeout=20000)

        msg = '[linuxDo] Login success'
        logging.info(msg)
        send_notifications(notifiers, '!! LinuxDo Login Success !!', msg)

        await asyncio.sleep(2)
        await context.storage_state(path=STATE_FILE)
        logging.info(f'[linuxDo] State saved to {STATE_FILE}')

    except Exception as e:
        err_msg = f'[linuxDo] Login failed: {e}'
        logging.error(err_msg)
        send_notifications(notifiers, '!! LinuxDo Login Error !!', err_msg)

    return context


async def oauth_authorize(context, page, account):
    """Perform OAuth authorization for an account.

    Returns:
        tuple: (api_user, cookies_dict) where api_user is a string user ID
               and cookies_dict is like {"session": "xxx"}.
               Returns (None, None) on failure.
    """
    name = account.get('name', 'Unknown')
    domain = account.get('domain', '')
    client_id = account.get('client_id', '')

    # Navigate to domain
    await page.goto(domain)
    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except:
        pass

    # Fetch OAuth state via API
    state_url = f'{domain}/api/oauth/state'
    logging.info(f'[{name}] Fetching OAuth state...')
    api_response = await context.request.get(state_url)
    try:
        state_data = json.loads(await api_response.text())
    except json.JSONDecodeError:
        state_data = {}
    state = state_data.get('data', '')

    if not state:
        logging.error(f'[{name}] Empty OAuth state')
        return None, None

    # Open LinuxDo Connect authorize page
    authorize_url = f'{CONNECT_URL}?response_type=code&client_id={client_id}&state={state}'
    logging.info(f'[{name}] Opening LinuxDo Connect authorize...')
    await page.goto(authorize_url, timeout=30000)
    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except:
        pass

    # Click authorize button if on approval page
    if 'connect.linux.do' in page.url:
        logging.info(f'[{name}] Clicking authorize...')
        try:
            btn = await page.wait_for_selector('a[href*="/oauth2/approve/"]', timeout=5000)
            await btn.click()
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
            except:
                pass
        except Exception as e:
            logging.warning(f'[{name}] No authorize button found: {e}')

    # Should be redirected back to domain
    if domain.rstrip('/') in page.url:
        logging.info(f'[{name}] OAuth success')

        # Get and parse localStorage.user
        user = await page.evaluate("""() => {
            const userData = localStorage.getItem('user');
            return userData ? JSON.parse(userData) : null;
        }""")
        api_user = str(user['id']) if user else None

        # Extract cookies as dict
        cookies = await context.cookies(domain)
        cookies_dict = {c['name']: c['value'] for c in cookies}

        logging.info(f'[{name}] api_user={api_user}')
        return api_user, cookies_dict
    else:
        logging.warning(f'[{name}] OAuth may have failed, ended at: {page.url}')
        return None, None
