"""
Unit tests for frontend date parsing and relative time (timeAgo) calculations.
Verifies that status timestamps never produce "NaNd ago", handle arbitrary
timezones (+HH:MM, Z, naive), SQLite UTC timestamps, and edge cases.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


class TestFrontendDashboardDateHandling(unittest.TestCase):
    """Test date parsing and timeAgo functions in frontend/src/main.js."""

    def setUp(self):
        super().setUp()
        self.frontend_js = Path(__file__).parent.parent / "frontend" / "src" / "main.js"
        self.frontend_html = Path(__file__).parent.parent / "frontend" / "index.html"
        self.node_bin = shutil.which("node")

    def test_frontend_main_js_exists(self):
        """Ensure frontend/src/main.js exists."""
        self.assertTrue(self.frontend_js.is_file())

    def test_frontend_html_contains_pacing_and_smart_glow_elements(self):
        """Ensure frontend/index.html defines pacing metrics and smart reminder configuration."""
        self.assertTrue(self.frontend_html.is_file())
        html = self.frontend_html.read_text(encoding="utf-8")
        self.assertIn('id="stat-pacing"', html)
        self.assertIn('id="pacing-expected-ml"', html)
        self.assertIn('id="pacing-bar"', html)
        self.assertIn('id="pacing-status-text"', html)
        self.assertIn('id="smart-pacing-summary"', html)
        self.assertIn('id="smart-pacing-expected"', html)
        self.assertIn('id="smart-pacing-needed"', html)
        self.assertIn('id="smart-pacing-status-pill"', html)
        self.assertIn('id="smart-reminder-toggle"', html)
        self.assertIn('id="smart-interval-select"', html)
        self.assertIn('id="smart-gentle-mode-select"', html)
        self.assertIn('id="smart-escalated-mode-select"', html)
        self.assertIn('id="smart-snooze-select"', html)
        self.assertIn('id="smart-autooff-select"', html)

    def test_led_mode_pickers_consistency_and_options(self):
        """Ensure all LED mode pickers (interactive controls, gentle reminder, escalated) have all 6 options."""
        import re

        html = self.frontend_html.read_text(encoding="utf-8")
        expected_modes = ["default", "breathe", "calm", "rainbow", "warmth", "christmas"]

        for select_id in ["led-select", "smart-gentle-mode-select", "smart-escalated-mode-select"]:
            match = re.search(rf'<select id="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL)
            self.assertIsNotNone(match, f"Select element {select_id} not found in index.html")
            block = match.group(1)
            options = re.findall(r'<option value="([^"]+)"', block)
            self.assertEqual(options, expected_modes, f"Select {select_id} does not have all 6 expected modes in order")
            self.assertNotIn("LED Mode:", block, f"Select {select_id} contains deprecated 'LED Mode:' prefix")

    def test_node_execution_parse_date_and_time_ago(self):
        """Run Node.js assertions directly against frontend/src/main.js."""
        if not self.node_bin:
            self.skipTest("Node.js binary not available in test environment")

        test_script = """
        await import('./frontend/src/main.js');
        const { parseDate, timeAgo } = globalThis;

        if (typeof parseDate !== 'function' || typeof timeAgo !== 'function') {
            throw new Error('parseDate or timeAgo not exported on globalThis');
        }

        // Test 1: Timezone offset string (+02:00) must NEVER return NaNd ago
        const resOffset = timeAgo('2026-09-26T23:20:40+02:00');
        if (resOffset.includes('NaN')) throw new Error('Offset produced NaN: ' + resOffset);

        // Test 2: Z suffix string
        const resZ = timeAgo('2026-09-26T21:20:40Z');
        if (resZ.includes('NaN')) throw new Error('Z suffix produced NaN: ' + resZ);

        // Test 3: SQLite UTC format (space separated)
        const resSqlite = timeAgo('2026-09-26 21:20:40');
        if (resSqlite.includes('NaN')) throw new Error('SQLite UTC produced NaN: ' + resSqlite);

        // Test 4: Current timestamp
        const resNow = timeAgo(new Date().toISOString());
        if (resNow !== '0s ago') throw new Error('Current time expected "0s ago", got: ' + resNow);

        // Test 5: Slight future timestamp (clock skew clamped to 0)
        const resFuture = timeAgo(Date.now() + 5000);
        if (resFuture !== '0s ago') throw new Error('Future time expected "0s ago", got: ' + resFuture);

        // Test 6: Exact elapsed seconds (< 60s)
        const res15s = timeAgo(Date.now() - 15000);
        if (res15s !== '15s ago') throw new Error('15s ago expected, got: ' + res15s);

        // Test 7: Exact elapsed minutes and seconds (< 1h)
        const res75s = timeAgo(Date.now() - 75000);
        if (res75s !== '1m 15s ago') throw new Error('1m 15s ago expected, got: ' + res75s);

        // Test 8: Exact elapsed hours, minutes and seconds (< 24h)
        const resHour = timeAgo(Date.now() - (3600 + 75) * 1000);
        if (resHour !== '1h 1m 15s ago') throw new Error('1h 1m 15s ago expected, got: ' + resHour);

        // Test 9: Invalid date strings must return 'never', not 'NaNd ago'
        if (timeAgo('invalid-date') !== 'never') throw new Error('Invalid string did not return never');
        if (timeAgo(null) !== 'never') throw new Error('Null did not return never');
        if (timeAgo(undefined) !== 'never') throw new Error('Undefined did not return never');
        if (timeAgo('') !== 'never') throw new Error('Empty string did not return never');

        // Test 10: parseDate handles Date instances, numbers, and strings
        const now = new Date();
        if (parseDate(now).getTime() !== now.getTime()) throw new Error('Date instance parsing failed');
        if (parseDate(1700000000000).getTime() !== 1700000000000) throw new Error('Number timestamp parsing failed');

        // Test 10b: Naive ISO timestamps from bottle logs (e.g. 08:00) must be parsed as local time without +2h shift
        const bottleSip = parseDate('2026-09-28T08:00:00');
        if (!bottleSip) throw new Error('Failed to parse bottle sip timestamp');
        if (bottleSip.getHours() !== 8) {
            throw new Error(`Bottle sip shifted! Expected local hour 8, got ${bottleSip.getHours()}`);
        }
        if (bottleSip.getMinutes() !== 0 || bottleSip.getSeconds() !== 0) {
            throw new Error('Bottle sip minutes/seconds mismatch');
        }
        const timeStr = bottleSip.toLocaleTimeString();
        if (!timeStr.includes('8:00') && !timeStr.includes('08:00')) {
            throw new Error(`toLocaleTimeString shifted! Expected 8:00, got: ${timeStr}`);
        }

        // Test 10c: Naive ISO with minutes and seconds
        const bottleSip2 = parseDate('2026-09-28T14:35:22');
        if (bottleSip2.getHours() !== 14 || bottleSip2.getMinutes() !== 35 || bottleSip2.getSeconds() !== 22) {
            throw new Error(`Bottle sip 2 shifted! Expected 14:35:22, got ${bottleSip2.getHours()}:${bottleSip2.getMinutes()}:${bottleSip2.getSeconds()}`);
        }

        // Test 10d: Space-separated SQLite UTC timestamp is parsed as UTC
        const sqliteDate = parseDate('2026-09-26 21:20:40');
        if (sqliteDate.toISOString() !== '2026-09-26T21:20:40.000Z') {
            throw new Error(`SQLite UTC expected 2026-09-26T21:20:40.000Z, got ${sqliteDate.toISOString()}`);
        }

        // Test 11: renderStatus and getLatestStatus exports
        const { renderStatus, getLatestStatus } = globalThis;
        if (typeof renderStatus !== 'function') throw new Error('renderStatus not a function');
        if (typeof getLatestStatus !== 'function') throw new Error('getLatestStatus not a function');
        renderStatus(); // Should safely no-op when document/latestStatus is null

        // Test 12: renderBattery and getLatestBattery exports
        const { renderBattery, getLatestBattery } = globalThis;
        if (typeof renderBattery !== 'function') throw new Error('renderBattery not a function');
        if (typeof getLatestBattery !== 'function') throw new Error('getLatestBattery not a function');

        renderBattery(88, false);
        const lb1 = getLatestBattery();
        if (!lb1 || lb1.battery !== 88 || lb1.charging !== false) throw new Error('getLatestBattery(88, false) failed');

        // Mock document DOM
        const mockElements = {
            'today-battery': { textContent: '' },
            'today-battery-unit': { style: { display: '' } },
            'battery-bar': { style: { width: '', background: '' } },
            'battery-charging-badge': { style: { display: '' } },
            'battery-status-text': { textContent: '', style: { color: '' } },
            'header-battery': { style: { display: '' }, className: '', title: '' },
            'header-battery-val': { textContent: '' },
            'header-battery-icon': { textContent: '' },
            'pacing-expected-ml': { textContent: '' },
            'pacing-bar': { style: { width: '', background: '' } },
            'pacing-status-text': { textContent: '', style: { color: '' } },
            'smart-pacing-expected': { textContent: '' },
            'smart-pacing-needed': { textContent: '', style: { color: '' } },
            'smart-pacing-status-pill': { textContent: '', style: { background: '', color: '' } },
        };
        globalThis.document = {
            getElementById: (id) => mockElements[id] || null,
            querySelectorAll: () => [],
        };

        // Test 13: Charging state (95%, charging)
        renderBattery(95, true);
        if (mockElements['today-battery'].textContent !== '95') throw new Error('today-battery expected 95');
        if (mockElements['battery-bar'].style.width !== '95%') throw new Error('battery-bar expected 95%');
        if (mockElements['battery-bar'].style.background !== '#38bdf8') throw new Error('charging bar expected cyan #38bdf8');
        if (mockElements['battery-charging-badge'].style.display !== 'inline-flex') throw new Error('badge expected inline-flex');
        if (!mockElements['battery-status-text'].textContent.includes('Charging')) throw new Error('status text expected Charging');
        if (mockElements['header-battery'].style.display !== 'inline-flex') throw new Error('header pill expected inline-flex');
        if (!mockElements['header-battery'].className.includes('charging')) throw new Error('header pill expected charging class');
        if (mockElements['header-battery-icon'].textContent !== '⚡') throw new Error('header icon expected ⚡');

        // Test 14: Discharging normal (80%, not charging)
        renderBattery(80, false);
        if (mockElements['today-battery'].textContent !== '80') throw new Error('today-battery expected 80');
        if (mockElements['battery-bar'].style.width !== '80%') throw new Error('battery-bar expected 80%');
        if (mockElements['battery-bar'].style.background !== 'var(--green)') throw new Error('normal bar expected var(--green)');
        if (mockElements['battery-charging-badge'].style.display !== 'none') throw new Error('badge expected none');
        if (mockElements['battery-status-text'].textContent !== 'Not charging') throw new Error('status text expected Not charging');
        if (mockElements['header-battery-icon'].textContent !== '🔋') throw new Error('header icon expected 🔋');

        // Test 15: Low battery (15%, not charging)
        renderBattery(15, false);
        if (mockElements['today-battery'].textContent !== '15') throw new Error('today-battery expected 15');
        if (mockElements['battery-bar'].style.background !== 'var(--red)') throw new Error('low bar expected var(--red)');
        if (mockElements['battery-status-text'].textContent !== 'Low Battery') throw new Error('status text expected Low Battery');
        if (mockElements['header-battery-icon'].textContent !== '🪫') throw new Error('header icon expected 🪫');
        if (!mockElements['header-battery'].className.includes('low')) throw new Error('header pill expected low class');

        // Test 16: Null / uninitialized battery
        renderBattery(null, null);
        if (mockElements['today-battery'].textContent !== '—') throw new Error('null battery expected —');
        if (mockElements['header-battery'].style.display !== 'none') throw new Error('null header pill expected hidden');
        if (mockElements['battery-status-text'].textContent !== 'Waiting for sync') throw new Error('null status expected Waiting for sync');

        // Test 17: Smart toggle text
        const { updateSmartToggleText, renderSmartBadge } = globalThis;
        mockElements['smart-toggle-label'] = { textContent: '', style: {} };
        mockElements['smart-reminders-badge'] = { textContent: '', style: {} };

        updateSmartToggleText(true);
        if (mockElements['smart-toggle-label'].textContent !== 'Smart Glow On') throw new Error('Expected Smart Glow On');
        updateSmartToggleText(false);
        if (mockElements['smart-toggle-label'].textContent !== 'Smart Glow Off') throw new Error('Expected Smart Glow Off');

        // Test 18: Render badge states
        renderSmartBadge({ enabled: false });
        if (mockElements['smart-reminders-badge'].textContent !== 'Disabled') throw new Error('Expected Disabled badge');

        renderSmartBadge({ enabled: true, state: 'gentle', idle_minutes: 42 });
        if (!mockElements['smart-reminders-badge'].textContent.includes('Gentle')) throw new Error('Expected Gentle badge');

        renderSmartBadge({ enabled: true, state: 'escalated', idle_minutes: 56 });
        if (!mockElements['smart-reminders-badge'].textContent.includes('Escalated')) throw new Error('Expected Escalated badge');

        renderSmartBadge({ enabled: true, state: 'snoozed', snooze_remaining_seconds: 480 });
        if (!mockElements['smart-reminders-badge'].textContent.includes('Snoozed')) throw new Error('Expected Snoozed badge');

        renderSmartBadge({ enabled: true, state: 'auto_off', idle_minutes: 70 });
        if (!mockElements['smart-reminders-badge'].textContent.includes('Away')) throw new Error('Expected Away badge');

        // Test 19: Pacing behind schedule (total 400ml, expected 650ml)
        const { renderPacing, getLatestPacing } = globalThis;
        if (typeof renderPacing !== 'function') throw new Error('renderPacing not a function');
        if (typeof getLatestPacing !== 'function') throw new Error('getLatestPacing not a function');

        renderPacing({ total_ml: 400, expected_ml: 650, goal_ml: 1800 });
        if (mockElements['pacing-expected-ml'].textContent !== '650') throw new Error('Expected 650ml expected target');
        if (mockElements['pacing-bar'].style.width !== '62%') throw new Error('Expected 62% bar width');
        if (mockElements['pacing-bar'].style.background !== '#fbbf24') throw new Error('Expected amber bar background');
        if (!mockElements['pacing-status-text'].textContent.includes('Drink 250 ml to reach target')) throw new Error('Expected drink 250 ml status');
        if (mockElements['smart-pacing-expected'].textContent !== '650 mL') throw new Error('Expected 650 mL smart pacing expected');
        if (mockElements['smart-pacing-needed'].textContent !== '250 mL') throw new Error('Expected 250 mL needed');
        if (!mockElements['smart-pacing-status-pill'].textContent.includes('Behind (250 mL)')) throw new Error('Expected Behind pill');

        const p1 = getLatestPacing();
        if (!p1 || p1.diff !== 250 || p1.on_track !== false) throw new Error('getLatestPacing behind state invalid');

        // Test 20: Pacing ahead of schedule (total 800ml, expected 650ml)
        renderPacing({ total_ml: 800, expected_ml: 650, goal_ml: 1800 });
        if (mockElements['pacing-bar'].style.width !== '100%') throw new Error('Expected 100% bar width');
        if (mockElements['pacing-bar'].style.background !== 'var(--green)') throw new Error('Expected green bar background');
        if (!mockElements['pacing-status-text'].textContent.includes('+150 ml ahead of pace')) throw new Error('Expected ahead of pace text');
        if (mockElements['smart-pacing-needed'].textContent !== '0 mL (On Track)') throw new Error('Expected 0 mL (On Track)');
        if (!mockElements['smart-pacing-status-pill'].textContent.includes('Ahead (+150 mL)')) throw new Error('Expected Ahead pill');

        const p2 = getLatestPacing();
        if (!p2 || p2.diff !== -150 || p2.on_track !== true) throw new Error('getLatestPacing ahead state invalid');

        // Test 21: Pacing on target (total 650ml, expected 650ml)
        renderPacing({ total_ml: 650, expected_ml: 650, goal_ml: 1800 });
        if (!mockElements['pacing-status-text'].textContent.includes('On target pace')) throw new Error('Expected on target pace text');
        if (mockElements['smart-pacing-status-pill'].textContent !== 'On Track') throw new Error('Expected On Track pill');

        // Test 22: Early morning before wake (expected 0ml)
        renderPacing({ total_ml: 0, expected_ml: 0, goal_ml: 1800, wake_time: '08:00' });
        if (mockElements['pacing-expected-ml'].textContent !== '0') throw new Error('Expected 0 expected target before wake');
        if (!mockElements['pacing-status-text'].textContent.includes('Day starts at 08:00')) throw new Error('Expected Day starts at 08:00');
        """

        res = subprocess.run(
            [self.node_bin, "--input-type=module", "-e", test_script],
            capture_output=True,
            text=True,
            cwd=str(self.frontend_js.parent.parent.parent),
        )
        self.assertEqual(res.returncode, 0, f"Node tests failed: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
