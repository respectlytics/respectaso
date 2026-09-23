"""The test runner refuses the network (core/test_runner.py), and says who asked.

Free-tier test: it guards the public build's runner as much as Pro's.
"""

import socket
import urllib.request

from django.test import SimpleTestCase

from core import test_runner


class TestsNeverReachTheNetworkTest(SimpleTestCase):
    def tearDown(self):
        # These tests are refused on purpose; their records are not failures.
        me = self.id()
        test_runner._refused[:] = [r for r in test_runner._refused if r[0] != me]

    def test_a_lookup_outside_this_machine_is_refused_and_recorded(self):
        with self.assertRaises(test_runner.NetworkBlockedInTests):
            socket.getaddrinfo("itunes.apple.com", 443)
        self.assertIn((self.id(), "itunes.apple.com"), test_runner._refused)

    def test_a_library_call_fails_at_once(self):
        with self.assertRaises(OSError):
            urllib.request.urlopen("https://itunes.apple.com/search?term=x", timeout=30)

    def test_this_machine_is_allowed(self):
        self.assertTrue(socket.getaddrinfo("localhost", 80))


class ARefusedTestFailsEvenWhenTheErrorWasCaughtTest(SimpleTestCase):
    """Services catch network errors and retry, so the refusal alone could
    pass unseen: the result fails the test that was refused."""

    class _Sample(SimpleTestCase):
        def runTest(self):
            pass

    def _check(self, result):
        sample = self._Sample()
        test_runner._refused.append((sample.id(), "itunes.apple.com"))
        result.startTest(sample)
        result.addSuccess(sample)
        result.stopTest(sample)
        self.assertFalse(result.wasSuccessful())
        self.assertNotIn(sample.id(), [r[0] for r in test_runner._refused])

    def test_in_a_serial_run(self):
        import io
        result_class = test_runner.NoNetworkTestRunner(verbosity=0).get_resultclass()
        self._check(result_class(io.StringIO(), False, 0))

    def test_in_a_parallel_worker(self):
        result_class = test_runner.NoNetworkParallelTestSuite.runner_class.resultclass
        self._check(result_class())

    def test_a_test_that_stayed_local_still_passes(self):
        import io
        result_class = test_runner.NoNetworkTestRunner(verbosity=0).get_resultclass()
        result = result_class(io.StringIO(), False, 0)
        sample = self._Sample()
        result.startTest(sample)
        result.addSuccess(sample)
        result.stopTest(sample)
        self.assertTrue(result.wasSuccessful())


class ParallelWorkersAreGuardedTest(SimpleTestCase):
    def test_every_worker_installs_the_guard_as_it_starts(self):
        suite = test_runner.NoNetworkTestRunner.parallel_test_suite
        self.assertIs(suite.process_setup, test_runner.install_guard)
        self.assertTrue(issubclass(suite.runner_class.resultclass, test_runner.RefusalsFailTheTest))

    def test_a_worker_failure_can_be_sent_back(self):
        import pickle
        try:
            raise AssertionError("expected 3, got 4")
        except AssertionError:
            import sys
            err = test_runner._as_text(sys.exc_info())
        sent = pickle.loads(pickle.dumps(err))
        self.assertIn("expected 3, got 4", str(sent[1]))
        self.assertIn("Traceback", str(sent[1]))
