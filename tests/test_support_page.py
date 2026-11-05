from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDR = "bc1qef95nyx6wrlyhnnzhhnz70qpan8j5tgvtdeh00"


def test_support_page_is_last_navigation_item_and_uses_fixed_address():
    js = (ROOT / "web" / "client.js").read_text(encoding="utf-8")
    assert "['settings','settings','Settings'],['support','heart','Support Development']" in js
    assert "function adminSupport()" in js
    assert "case'support':return adminSupport()" in js
    assert "navigator.clipboard.writeText(ORICTUX_BITCOIN_ADDRESS)" in js
    assert f"const ORICTUX_BITCOIN_ADDRESS = '{ADDR}'" in js
    assert "supportCopy[admin?.settings?.admin_language]" in js


def test_support_address_is_outside_translation_settings_and_is_opt_in():
    js = (ROOT / "web" / "client.js").read_text(encoding="utf-8")
    assert js.count(ADDR) == 1
    assert "Support Development" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert ADDR in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "custom:" in (ROOT / ".github/FUNDING.yml").read_text(encoding="utf-8")
    assert "support-btc-address" in js
    assert "data-tab=\"${id}\"" in js


def test_public_docs_have_no_long_dashes_or_old_brand():
    for f in (ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "RELEASE_NOTES.md"):
        text = f.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text


def test_support_qr_is_exact_wallet_and_public_asset():
    from PIL import Image
    qr = ROOT / 'web' / 'support-btc-qr.png'
    repo_qr = ROOT / 'assets' / 'branding' / 'orictux-btc-qr.png'
    assert qr.read_bytes() == repo_qr.read_bytes()
    assert Image.open(qr).size[0] >= 300
    assert ADDR in (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '/support-btc-qr.png' in (ROOT / 'web' / 'client.js').read_text(encoding='utf-8')
    assert "'support-btc-qr.png'" in (ROOT / 'app' / 'main.py').read_text(encoding='utf-8')


def test_readme_badge_links_and_brand_assets():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    for slug in ('release', 'features', 'quick-start', 'documentation', 'self-hosting', 'platforms', 'mit-license', 'donate-btc'):
        assert (ROOT / 'assets' / 'badges' / f'{slug}.svg').is_file()
        assert f'assets/badges/{slug}.svg' in readme
    assert 'assets/branding/orictux-logo.png' in readme
    assert 'assets/branding/datewithme-hero.png' in readme
    assert 'assets/branding/orictux-btc-qr.png' in readme
    assert 'DateWithBurak' not in readme


def test_official_logo_is_available_on_support_page():
    assert (ROOT / 'web' / 'orictux-logo.png').is_file()
    assert '/orictux-logo.png' in (ROOT / 'web' / 'client.js').read_text(encoding='utf-8')
    assert "'orictux-logo.png'" in (ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
