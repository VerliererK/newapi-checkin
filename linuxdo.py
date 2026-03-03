import os
import json
import logging

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


async def _create_context_with_cookies(browser, cookie_string):
    """Create a browser context with manually provided LinuxDo cookies."""
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={'width': 800, 'height': 600},
        ignore_https_errors=True,
    )

    cookies = []
    for part in cookie_string.split(';'):
        part = part.strip()
        if '=' in part:
            name, value = part.split('=', 1)
            cookies.append({
                'name': name.strip(),
                'value': value.strip(),
                'domain': '.linux.do',
                'path': '/',
            })

    if cookies:
        await context.add_cookies(cookies)

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


def _parse_oauth_state(body):
    """Extract OAuth state from API JSON response body."""
    try:
        state_data = json.loads(body)
    except json.JSONDecodeError:
        return ''

    state = state_data.get('data', '')
    return state if isinstance(state, str) else ''


async def _fetch_oauth_state(page, name, domain):
    logging.info(f'[{name}] Fetching OAuth state...')

    state_url = f'{domain}/api/oauth/state'
    try:
        result = await page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json, text/plain, */*'
                    }
                });
                return {
                    ok: res.ok,
                    status: res.status,
                    text: await res.text()
                };
            }""",
            state_url,
        )
    except Exception as e:
        logging.warning(f'[{name}] Fetch OAuth state failed in page: {e}')
        result = {'ok': False, 'status': 0, 'text': ''}

    body = result.get('text', '') or ''
    state = _parse_oauth_state(body)
    if state:
        return state

    status = result.get('status')
    logging.error(f'[{name}] Empty OAuth state (status={status}), body: {body}')
    return ''


async def login_linuxdo(browser):
    # Priority 1: Try saved state file
    if os.path.exists(STATE_FILE):
        logging.info('[linuxDo] Found saved state, attempting to restore session...')
        try:
            context, page = await _create_context_with_state(browser)
            if await _is_logged_in(page):
                logging.info('[linuxDo] Session restored from saved state')
                return context
            else:
                logging.info('[linuxDo] Saved state expired')
                await context.close()
        except Exception as e:
            logging.warning(f'[linuxDo] Failed to restore state: {e}')

    # Priority 2: Try LINUXDO_COOKIE env var
    cookie_str = os.environ.get('LINUXDO_COOKIE')
    if cookie_str:
        logging.info('[linuxDo] Trying cookies from LINUXDO_COOKIE env var...')
        try:
            context, page = await _create_context_with_cookies(browser, cookie_str)
            if await _is_logged_in(page):
                logging.info('[linuxDo] Cookie login success')
                await context.storage_state(path=STATE_FILE)
                return context
            else:
                logging.warning('[linuxDo] Cookies from env var are invalid/expired')
                await context.close()
        except Exception as e:
            logging.warning(f'[linuxDo] Failed to use LINUXDO_COOKIE: {e}')

    logging.error('[linuxDo] No valid session available')
    return None


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

    # Fetch OAuth state via in-page fetch (handles Cloudflare better than direct API requests)
    state = await _fetch_oauth_state(page, name, domain)

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
