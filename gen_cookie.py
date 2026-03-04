"""Open a browser for manual LinuxDo login, then save cookie and state."""

import argparse
import os
from playwright.sync_api import sync_playwright

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'linuxdo_state.json')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'


def main():
    parser = argparse.ArgumentParser(description='Checkin')
    parser.add_argument('--channel', type=str, default='chromium', help='Browser channel')
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel=args.channel)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 800, 'height': 600},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

        page.goto('https://linux.do/login')
        print('Please login in the browser window...')

        # Wait for login success (max 5 minutes)
        page.wait_for_selector('#current-user', timeout=300000)
        print('Login detected!')

        # Save state for linuxdo.py
        context.storage_state(path=STATE_FILE)
        print(f'State saved to {STATE_FILE}')

        # Extract cookie string for GitHub Secrets
        cookies = context.cookies('https://linux.do')
        cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in cookies if c['name'] in ('_t', '_forum_session'))
        print(f'\nLINUXDO_COOKIE={cookie_str}')
        print('\nCopy the above value to GitHub Secrets if needed.')

        browser.close()


if __name__ == '__main__':
    main()
