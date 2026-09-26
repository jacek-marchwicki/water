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
        self.node_bin = shutil.which("node")

    def test_frontend_main_js_exists(self):
        """Ensure frontend/src/main.js exists."""
        self.assertTrue(self.frontend_js.is_file())

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

        // Test 11: renderStatus and getLatestStatus exports
        const { renderStatus, getLatestStatus } = globalThis;
        if (typeof renderStatus !== 'function') throw new Error('renderStatus not a function');
        if (typeof getLatestStatus !== 'function') throw new Error('getLatestStatus not a function');
        renderStatus(); // Should safely no-op when document/latestStatus is null
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
