"""One reproducible browser walkthrough of synthetic mode; no live API claims."""
from pathlib import Path
import argparse
import os
import re
import json
import sys
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import PantaCatalog, ThreadingHTTPServer, make_handler

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--offline', action='store_true', help='Render source in memory with explicit synthetic API replay; no browser network navigation.')
args = parser.parse_args()
out = ROOT / 'evidence'; out.mkdir(exist_ok=True)
server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(PantaCatalog()))
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
base = f'http://127.0.0.1:{server.server_port}'
errors, api_paths = [], []
catalog_fixture = PantaCatalog()
def load_page(page):
    if not args.offline:
        page.goto(base)
    else:
        # Exercise the UI without attempting the blocked network path or changing browser policy.
        html = (ROOT / 'public/index.html').read_text()
        html = re.sub(r'<link rel="stylesheet"[^>]+>', '', html)
        html = re.sub(r'<script type="module"[^>]*></script>', '', html)
        page.set_content(html)
        page.add_style_tag(content=(ROOT / 'public/styles.css').read_text())
        def fixture_read(path):
            api_paths.append(path + ' [offline replay]')
            if path == '/api/config': return {'defaultMode': 'demo', 'liveConfigured': False}
            if path == '/api/demo': return catalog_fixture.demo()
            if path.startswith('/api/demo/'): return catalog_fixture.demo(path.rsplit('/', 1)[-1])
            raise ValueError('Unexpected offline fixture request: ' + path)
        page.expose_function('radarFixtureRead', fixture_read)
        page.evaluate("window.fetch = async path => ({ok:true, status:200, json:async()=>await window.radarFixtureRead(String(path))})")
        model_url = page.evaluate("source => URL.createObjectURL(new Blob([source], {type:'text/javascript'}))", (ROOT / 'public/model.mjs').read_text())
        app = (ROOT / 'public/app.mjs').read_text().replace("'./model.mjs'", json.dumps(model_url))
        page.add_script_tag(content=app, type='module')
    page.wait_for_function("document.querySelector('#count-loaded').textContent === '8'")

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('RADAR_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 960}, device_scale_factor=1,
            record_video_dir=str(out / 'video'), record_video_size={'width': 1440, 'height': 960}, accept_downloads=True)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: api_paths.append(request.url.split('/api/')[-1]) if '/api/' in request.url else None)
        started = time.perf_counter()
        load_page(page)
        load_ms = (time.perf_counter() - started) * 1000
        assert page.locator('#count-due').inner_text() == '1'
        assert page.locator('#count-soon').inner_text() == '3'
        assert page.locator('#count-unscheduled').inner_text() == '1'
        assert 'synthetic' in page.locator('#mode-notice').inner_text()
        assert page.locator('#mode option[value="live"]').is_disabled()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / 'desktop-overview.png'), full_page=True)
        page.locator('[data-timing="due"]').click(); assert page.locator('.market-row').count() == 1
        page.locator('.market-open').first.click()
        page.wait_for_function("document.querySelector('#detail-content').textContent.includes('52¢')")
        page.wait_for_timeout(1300)
        page.locator('.watch-button').first.click(); page.locator('#watch-tab').click()
        assert page.locator('.market-row').count() == 1
        with page.expect_download() as download:
            page.locator('#export-json').click()
        snapshot_path = out / 'demo-snapshot.json'; download.value.save_as(str(snapshot_path))
        snapshot = json.loads(snapshot_path.read_text())
        assert snapshot['source']['mode'] == 'demo' and len(snapshot['items']) == 1
        page.wait_for_timeout(1000)
        page.locator('#all-tab').click(); page.locator('#timing').select_option('soon')
        assert page.locator('.market-row').count() == 3
        with page.expect_download() as download:
            page.locator('#export-ics').click()
        calendar_path = out / 'demo-schedules.ics'; download.value.save_as(str(calendar_path))
        ics = calendar_path.read_text()
        assert ics.count('BEGIN:VEVENT') == 3 and '[DEMO]' in ics
        page.wait_for_timeout(1000)
        page.locator('#search').fill('Cedar'); assert page.locator('.market-row').count() == 1
        page.locator('.market-open').click()
        page.wait_for_function("document.querySelector('#detail-content h2').textContent.includes('Cedar')")
        page.wait_for_timeout(1000)
        page.locator('#search').fill(''); page.locator('#timing').select_option('')
        page.locator('#markets').scroll_into_view_if_needed()
        page.screenshot(path=str(out / 'desktop-detail.png'), full_page=True)
        page.wait_for_timeout(1800)
        video_path = str(page.video.path())
        context.close()
        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, device_scale_factor=1)
        small = mobile.new_page(); small.on('pageerror', lambda error: errors.append(str(error)))
        load_page(small)
        assert small.evaluate('document.documentElement.scrollWidth <= innerWidth')
        small.screenshot(path=str(out / 'mobile-overview.png'), full_page=True)
        mobile.close(); browser.close()
        assert errors == [], errors
        receipt = {'mode': 'synthetic demo, not live Panta', 'transport': 'in-memory UI replay; browser localhost denied by administrator' if args.offline else 'loopback HTTP', 'desktop': '1440x960', 'mobile': '390x844',
          'initialVisibleCatalogMs': round(load_ms, 3), 'browser': 'Chromium via Playwright',
          'pageErrors': errors, 'apiRequestPaths': api_paths,
          'checks': ['8 rows / due=1 / soon=3 / missing=1', 'demo labeling and absent-key live control',
              'schedule filtering', 'detail read and separate spot prices', 'watchlist filtering',
              'one-row JSON provenance export', 'three-event tentative demo calendar', 'search', 'mobile no horizontal overflow'],
          'videoSource': Path(video_path).name}
        (out / 'browser-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt, indent=2))
finally:
    server.shutdown(); thread.join(timeout=3); server.server_close()
