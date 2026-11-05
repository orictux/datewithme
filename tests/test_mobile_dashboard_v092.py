"""Mobile dashboard layout regressions introduced in 0.9.2-beta."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_visit_records_have_mobile_labels_and_stay_selectable():
    source = (ROOT / 'web/client.js').read_text(encoding='utf-8')
    view = source.split('function adminVisits(){', 1)[1].split('// Admin-only preview:', 1)[0]
    for label in ('Visitor', 'Device', 'No attempts', 'Stage', 'Last choice', 'Visited'):
        assert f'data-label="{label}"' in view
    assert 'mobile-record-cards' in view
    assert 'responsive-record-table' in view
    assert 'id="all-visits"' in view
    assert 'class="visit-check"' in view


def test_date_records_have_mobile_labels_and_actions():
    source = (ROOT / 'web/client.js').read_text(encoding='utf-8')
    view = source.split('function adminPlans(){', 1)[1].split('function adminCalendar(){', 1)[0]
    for label in ('Person / Code', 'Activity', 'Date / Time', 'Status', 'No', 'Cancellation Views', 'Actions'):
        assert f'data-label="{label}"' in view
    assert 'mobile-record-cards' in view
    assert 'id="all-plans"' in view
    assert 'class="plan-check"' in view
    assert 'data-delete-plan' in view


def test_mobile_css_and_revisioned_assets_are_present():
    css = (ROOT / 'web/layout.css').read_text(encoding='utf-8')
    html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    assert 'grid-template-columns:minmax(0,1fr)' in css
    assert 'font-size:16px;' in css
    assert '.responsive-record-table tbody tr' in css
    assert '.admin-side .admin-logout' in css
    assert '/layout.css?v=1.4.9' in html
    assert '/client.js?v=1.4.9' in html
