"""
NewAPI 自動簽到腳本 (簡化版)

整體流程：
  1. 讀設定 (config.json 或環境變數)
  2. 讀 cookies 快取
  3. 開瀏覽器 → 逐個帳號：帶 cookie → 查額度 → 簽到 → 再查額度
  4. 收工
"""

import os
import json
import asyncio
import logging
import argparse
from playwright.async_api import async_playwright

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s', level=logging.INFO)

# ── 常數 ──────────────────────────────────────────────

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
COOKIES_CACHE_FILE = 'cookies_cache.json'
QUOTA_DIVISOR = 500000


# ── Step 1: 讀設定 ────────────────────────────────────


def load_config():
    """從環境變數或 config.json 讀取帳號清單。"""
    accounts_env = os.environ.get('CHECKIN_ACCOUNTS')
    if accounts_env:
        logging.info('從環境變數讀取設定')
        return json.loads(accounts_env)

    if os.path.exists('config.json'):
        logging.info('從 config.json 讀取設定')
        with open('config.json', encoding='utf-8') as f:
            return json.load(f).get('accounts', [])

    raise RuntimeError('找不到設定，請設 CHECKIN_ACCOUNTS 環境變數或建 config.json')


# ── Step 2: 讀寫 cookies 快取 ─────────────────────────


def load_cookies_cache():
    """從環境變數或快取檔讀取 cookies，環境變數優先。"""
    env = os.environ.get('COOKIES_CACHE')
    if env:
        logging.info('從 COOKIES_CACHE 環境變數讀取 cookies 快取')
        return json.loads(env)
    if os.path.exists(COOKIES_CACHE_FILE):
        with open(COOKIES_CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cookies_cache(cache):
    """把 cookies 寫回快取檔。"""
    with open(COOKIES_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2)


# ── Step 3: 查額度 ────────────────────────────────────


async def get_quota(page, domain, api_user):
    """呼叫 /api/user/self 取得目前餘額。"""
    await page.set_extra_http_headers({'new-api-user': api_user})
    resp = await page.goto(f'{domain}/api/user/self')
    data = json.loads(await resp.text())
    quota = round(data['data']['quota'] / QUOTA_DIVISOR, 2)
    used = round(data['data']['used_quota'] / QUOTA_DIVISOR, 2)
    logging.info(f'餘額: ${quota}, 已用: ${used}')
    return quota


# ── Step 4: 執行簽到 ──────────────────────────────────


async def do_checkin(page, domain, api_user, endpoint='/api/user/sign_in'):
    """對 NewAPI 站發 POST 簽到。"""
    url = f'{domain}{endpoint}'
    await page.set_extra_http_headers({'new-api-user': api_user})
    result = await page.evaluate(f"fetch('{url}', {{method: 'POST'}}).then(r => r.text())")
    logging.info(f'簽到結果: {result}')


# ── 工具函數 ──────────────────────────────────────────


async def hide_webdriver(page):
    """讓 Playwright 不被偵測為自動化瀏覽器。"""
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")


async def wait_network_idle(page, timeout=10000):
    """等網路請求都完成，逾時就算了不卡住。"""
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
    except Exception:
        pass


async def make_page_with_cookies(browser, domain, cookies_dict):
    """用已有的 cookies 建一個新 page，直接帶 cookie 進去。"""
    bare_domain = domain.replace('https://', '').replace('http://', '')
    context = await browser.new_context(user_agent=USER_AGENT, ignore_https_errors=True)
    await context.add_cookies([{'name': k, 'value': v, 'domain': bare_domain, 'path': '/'} for k, v in cookies_dict.items()])
    page = await context.new_page()
    await hide_webdriver(page)
    await page.goto(domain)
    await wait_network_idle(page)
    return context, page


# ── 主流程 ────────────────────────────────────────────


async def process_account(browser, account, cookies_cache):
    """處理單一帳號：帶快取 cookie → 查額度 → 簽到 → 再查額度。"""
    name = account.get('name', '?')
    domain = account['domain']
    endpoint = account.get('endpoint', '/api/user/sign_in')

    logging.info(f'====== [{name}] 開始處理 ======')

    cached = cookies_cache.get(domain)
    if not cached:
        logging.error(f'[{name}] 沒有快取 cookie，跳過')
        return

    api_user = cached['api_user']
    ctx, page = await make_page_with_cookies(browser, domain, cached['cookies'])
    try:
        old = await get_quota(page, domain, api_user)
        await do_checkin(page, domain, api_user, endpoint)
        new = await get_quota(page, domain, api_user)
        if new > old:
            logging.info(f'[{name}] 簽到成功! 額度 {old} → {new}')
    finally:
        await ctx.close()


def parse_args():
    parser = argparse.ArgumentParser(description='NewAPI 自動簽到')
    parser.add_argument('--channel', type=str, default='chromium', help='瀏覽器 channel (預設: chromium)')
    parser.add_argument('--no-headless', action='store_true', help='顯示瀏覽器視窗')
    return parser.parse_args()


async def main():
    args = parse_args()

    # 1. 讀設定
    accounts = load_config()
    cookies_cache = load_cookies_cache()

    async with async_playwright() as p:
        # 2. 開瀏覽器
        browser = await p.chromium.launch(headless=not args.no_headless, channel=args.channel)

        # 3. 逐個帳號簽到
        for account in accounts:
            if account.get('disabled'):
                continue
            try:
                await process_account(browser, account, cookies_cache)
            except Exception as e:
                logging.error(f'[{account.get("name", "?")}] 出錯: {e}')

        # 4. 收尾
        save_cookies_cache(cookies_cache)
        await browser.close()

    logging.info('全部完成!')


if __name__ == '__main__':
    asyncio.run(main())
