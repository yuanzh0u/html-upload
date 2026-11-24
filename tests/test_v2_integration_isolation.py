"""Regression coverage for the integration fixture's read-only import boundary."""
import subprocess
import sys
import unittest
from pathlib import Path


class IsolationTests(unittest.TestCase):
    def check_child(self, script):
        tests = Path(__file__).resolve().parent
        script = 'import sys\nsys.path.insert(0, sys.argv[1])\n' + script
        result = subprocess.run([sys.executable, '-c', script, str(tests)], cwd=tests,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_preserves_existing_bytecode_guard(self):
        self.check_child("""
import sys
sys.dont_write_bytecode = True
import test_v2_integration as integration
assert sys.dont_write_bytecode is True, 'integration import cleared existing guard'
""")

    def test_setup_guards_service_and_restores_flag_after_cleanup(self):
        self.check_child("""
import sys
from unittest.mock import patch
import test_v2_integration as integration
if integration.SKIP:
    raise SystemExit(0)
class StopSetup(Exception):
    pass
for prior in (False, True):
    sys.dont_write_bytecode = prior
    observed = []
    def stop(test):
        observed.append(sys.dont_write_bytecode)
        raise StopSetup
    case = integration.IntegrationTests('test_full_flow')
    try:
        with patch.object(integration.support, 'file_db', stop):
            try:
                case.setUp()
            except StopSetup:
                pass
            assert observed == [True], 'service setup ran without bytecode guard'
            assert sys.dont_write_bytecode is True
    finally:
        case.doCleanups()
    assert sys.dont_write_bytecode is prior, 'cleanup did not restore caller flag'
""")

    def test_setup_failure_closes_bound_server_without_shutdown(self):
        self.check_child("""
import sys
from unittest.mock import patch
import test_v2_integration as integration
if integration.SKIP:
    raise SystemExit(0)
class StopSetup(Exception):
    pass
for stage in ('api', 'set_app', 'thread_start'):
    for prior in (False, True):
        sys.dont_write_bytecode = prior
        events = []
        class Server:
            server_port = 19099
            def set_app(self, app):
                if stage == 'set_app':
                    raise StopSetup
            def serve_forever(self):
                raise AssertionError('fake thread must not serve')
            def server_close(self):
                events.append(('close', sys.dont_write_bytecode))
            def shutdown(self):
                events.append(('shutdown', sys.dont_write_bytecode))
        def make_api(*args, **kwargs):
            if stage == 'api':
                raise StopSetup
            return object()
        case = integration.IntegrationTests('test_full_flow')
        with patch.object(integration, 'make_server', return_value=Server()), \
             patch.object(integration, 'AgentAPI', side_effect=make_api), \
             patch.object(integration.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = StopSetup
            try:
                case.setUp()
            except StopSetup:
                pass
            else:
                raise AssertionError('expected setup failure')
            finally:
                case.doCleanups()
        assert events == [('close', True)], (stage, events)
        assert sys.dont_write_bytecode is prior, 'cleanup did not restore caller flag'
""")
