from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('node'), 'Node is required for browser behavior checks')
class ReconnectBehaviorTests(unittest.TestCase):
    def test_handoff_precedes_radio_switch_and_restores_rejected_form(self):
        result = subprocess.run([shutil.which('node'), str(ROOT/'tests/reconnect_behavior.cjs'), str(ROOT/'src/magic_box/static/admin.js')], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
