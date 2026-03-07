"""
本地 Cookie 產生器

使用方式：
  python gen_cookies.py

流程：
  1. 開瀏覽器 → 導到 linux.do 登入頁
  2. 等你手動登入（過 hCaptcha）
  3. 登入後自動對每個帳號跑 OAuth 拿 cookie
  4. 存到 cookies_cache.json
"""

import os
import json
import asyncio
import logging
import argparse
from playwright.async_api import async_playwright

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
LINUXDO_CONNECT_URL = 'https://connect.linux.do/oauth2/authorize'
COOKIES_CACHE_FILE = 'cookies_cache.json'
QUOTA_DIVISOR = 500000


# ── 讀設定 ────────────────────────────────────────────


def load_accounts():
    """從 config.json 讀帳號清單。"""
    if os.path.exists('config.json'):
        with open('config.json', encoding='utf-8') as f:
            return json.load(f).get('accounts', [])
    raise RuntimeError('找不到 config.json')


def load_cookies_cache():
    """讀現有的 cookies 快取。"""
    if os.path.exists(COOKIES_CACHE_FILE):
        with open(COOKIES_CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


# ── 檢查 cookie 是否有效 ─────────────────────────────


async def check_cookie_valid(browser, domain, api_user, cookies_dict):
    """帶快取 cookie 呼叫 /api/user/self，能拿到資料就是有效。"""
    bare_domain = domain.replace('https://', '').replace('http://', '')
    context = await browser.new_context(user_agent=USER_AGENT, ignore_https_errors=True)
    await context.add_cookies([{'name': k, 'value': v, 'domain': bare_domain, 'path': '/'} for k, v in cookies_dict.items()])
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

    try:
        await page.goto(domain)
        await page.set_extra_http_headers({'new-api-user': api_user})
        resp = await page.goto(f'{domain}/api/user/self')
        data = json.loads(await resp.text())
        quota = round(data['data']['quota'] / QUOTA_DIVISOR, 2)
        logging.info(f'Cookie 有效，餘額 ${quota}')
        return True
    except Exception:
        return False
    finally:
        await context.close()


# ── 等待手動登入 ──────────────────────────────────────


async def wait_for_login(page):
    """開 linux.do，等使用者手動登入（過 hCaptcha）。"""
    await page.goto('https://linux.do')

    # 先看有沒有已登入
    try:
        await page.wait_for_selector('#current-user', timeout=3000)
        logging.info('已經是登入狀態，跳過')
        return
    except Exception:
        pass

    logging.info('請在瀏覽器中登入 LinuxDo...')
    # 無限等待，直到使用者登入完成
    await page.wait_for_selector('#current-user', timeout=0)
    logging.info('LinuxDo 登入成功!')


# ── OAuth 授權 ────────────────────────────────────────


async def fetch_oauth_state(page, domain):
    """跟 NewAPI 站拿 OAuth state token。"""
    result = await page.evaluate(
        """async (url) => {
            const res = await fetch(url, { method: 'GET', credentials: 'include' });
            return await res.text();
        }""",
        f'{domain}/api/oauth/state',
    )
    return json.loads(result).get('data', '')


async def oauth_authorize(context, account):
    """對一個帳號做 OAuth，回傳 (api_user, cookies_dict)。"""
    name = account.get('name', '?')
    domain = account['domain']
    client_id = account['client_id']

    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

    # 1. 開目標站拿 state
    await page.goto(domain)
    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except Exception:
        pass

    state = await fetch_oauth_state(page, domain)
    if not state:
        logging.error(f'[{name}] 拿不到 OAuth state')
        await page.close()
        return None, None

    # 2. 跳到 LinuxDo Connect 授權
    auth_url = f'{LINUXDO_CONNECT_URL}?response_type=code&client_id={client_id}&state={state}'
    logging.info(f'[{name}] OAuth 授權中...')
    await page.goto(auth_url, timeout=30000)
    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except Exception:
        pass

    # 3. 按授權按鈕（如果有的話）
    if 'connect.linux.do' in page.url:
        try:
            btn = await page.wait_for_selector('a[href*="/oauth2/approve/"]', timeout=5000)
            await btn.click()
            try:
                await page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass
        except Exception:
            pass

    # 4. 確認回到目標站，撈資料
    if domain.rstrip('/') not in page.url:
        logging.warning(f'[{name}] OAuth 失敗，停在: {page.url}')
        await page.close()
        return None, None

    user = await page.evaluate("""() => {
        const d = localStorage.getItem('user');
        return d ? JSON.parse(d) : null;
    }""")
    api_user = str(user['id']) if user else None
    cookies = await context.cookies(domain)
    cookies_dict = {c['name']: c['value'] for c in cookies}

    logging.info(f'[{name}] 取得 api_user={api_user}')
    await page.close()
    return api_user, cookies_dict


# ── 主流程 ────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description='產生 NewAPI cookies 快取')
    parser.add_argument('--channel', type=str, default='chromium', help='瀏覽器 channel (預設: chromium)')
    return parser.parse_args()


async def main():
    args = parse_args()
    accounts = load_accounts()
    active = [a for a in accounts if not a.get('disabled')]
    logging.info(f'共 {len(active)} 個帳號需要取得 cookie')

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel=args.channel)

        # 0. 讀現有快取，先檢查哪些需要重新取得
        cache = load_cookies_cache()
        need_oauth = []
        for account in active:
            name = account.get('name', '?')
            domain = account['domain']
            cached = cache.get(domain)
            if cached:
                logging.info(f'[{name}] 檢查現有 cookie...')
                if await check_cookie_valid(browser, domain, cached['api_user'], cached['cookies']):
                    logging.info(f'[{name}] Cookie 仍有效，跳過')
                    continue
                else:
                    logging.info(f'[{name}] Cookie 已過期，需要重新取得')
            need_oauth.append(account)

        if not need_oauth:
            logging.info('所有帳號的 cookie 都還有效，不需要登入 LinuxDo')
            await browser.close()
        else:
            logging.info(f'共 {len(need_oauth)} 個帳號需要重新 OAuth')

            context = await browser.new_context(
                user_agent=USER_AGENT,
                ignore_https_errors=True,
            )
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            # 1. 等使用者登入 LinuxDo
            await wait_for_login(page)
            await page.close()

            # 2. 逐個帳號 OAuth
            for account in need_oauth:
                name = account.get('name', '?')
                try:
                    api_user, cookies_dict = await oauth_authorize(context, account)
                    if api_user:
                        cache[account['domain']] = {'api_user': api_user, 'cookies': cookies_dict}
                        logging.info(f'[{name}] OK')
                    else:
                        logging.error(f'[{name}] 失敗')
                except Exception as e:
                    logging.error(f'[{name}] 出錯: {e}')

            await context.close()
            await browser.close()

    # 3. 存檔
    with open(COOKIES_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)

    logging.info(f'已存到 {COOKIES_CACHE_FILE}，共 {len(cache)} 個帳號')

    # 印出來方便貼到 GitHub Variable
    print()
    print('=== 以下內容貼到 GitHub Variable COOKIES_CACHE ===')
    print(json.dumps(cache))


if __name__ == '__main__':
    asyncio.run(main())
