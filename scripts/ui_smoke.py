"""Optional real-browser smoke test (requires Playwright and installed Chromium).

Start a fixture-mode API against a disposable database with two review-state demo
jobs, then set UI_TEST_URL and ADMIN_TOKEN. This approves one synthetic record.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

url = os.environ.get('UI_TEST_URL', 'http://127.0.0.1:8123')
token = os.environ['ADMIN_TOKEN']
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url)
    page.locator('#token').fill(token)
    page.locator('#connect').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('fixture')")
    assert page.locator('#token').input_value() == ''
    page.locator('#connect').click()
    page.wait_for_timeout(300)
    assert 'required' not in page.locator('#message').inner_text().lower()
    assert 'fixture' in page.locator('#status').inner_text()
    page.locator('#jobs button').last.click()
    page.wait_for_function("!document.querySelector('#review').hidden")
    assert 'SYNTHETIC' in page.locator('#source').inner_text()
    page.locator('#reviewer').fill('Synthetic demo reviewer')
    page.locator('#note').fill('Checked the synthetic source and contact labels')
    screenshot = os.environ.get('UI_SCREENSHOT')
    if screenshot:
        Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=screenshot, full_page=True)
    for checkbox in ('check-identity','check-evidence','check-contacts','check-registration','check-duplicates'):
        page.locator('#'+checkbox).check()
    page.locator('#approve').click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('Approved')")
    assert page.locator('#approve').is_disabled()
    with page.expect_download() as download_info:
        page.locator('#export').click()
    assert download_info.value.suggested_filename == 'approved_directory.xlsx'
    page.locator('#forget').click()
    assert page.locator('#status').inner_text() == 'Disconnected'
    assert page.locator('#review').is_hidden()
    assert not errors, errors
    browser.close()
print('Browser smoke passed: login, repeated refresh, source review, human approval, Excel download, logout')
