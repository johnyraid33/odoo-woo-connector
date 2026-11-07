"""Render the store cover and the app icon from their HTML sources.

Needs Google Chrome. Run from the repository root:

    python3 dev/store/render_assets.py
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESC = ROOT / 'rex_woocommerce_connector' / 'static' / 'description'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
ASSETS = [
    # source, window size, saved size, targets
    ('cover.html', (1600, 900), None, [DESC / 'img' / 'cover.png']),
    # drawn at 768 px (headless Chrome has a minimum window width), saved at 256
    ('icon.html', (768, 768), 256, [DESC / 'icon.png']),
]


def render(source, size, out, tmp):
    subprocess.run([
        CHROME, '--headless=new', '--disable-gpu', '--hide-scrollbars',
        '--allow-file-access-from-files', '--user-data-dir=%s/profile' % tmp,
        '--force-device-scale-factor=1', '--window-size=%s,%s' % size,
        '--screenshot=%s' % out, (Path(__file__).parent / source).as_uri(),
    ], check=True, capture_output=True)


with tempfile.TemporaryDirectory() as tmp:
    for source, size, saved, targets in ASSETS:
        out = Path(tmp) / source.replace('.html', '.png')
        render(source, size, out, tmp)
        if saved:
            subprocess.run(['sips', '-Z', str(saved), str(out)], check=True, capture_output=True)
        for target in targets:
            shutil.copyfile(out, target)
            print('wrote', target.relative_to(ROOT))
