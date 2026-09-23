"""The test runner: tests never reach the internet.

A test that talks to Apple passes while Apple answers quickly and hangs
when Apple throttles the machine. On 2026-09-23, after a calibration study
had used up this Mac's request budget, one Simulator test that had never
mocked the iTunes client sat in retry backoff for 17 minutes. This runner
makes any connection or name lookup outside this machine fail at once, with
a message naming the host. Services here catch network errors and retry,
so the refusal alone could pass unseen: every refused attempt is also
recorded against the test that made it, and the run fails with that list.
A test that forgot to mock a service therefore fails on its first run,
instead of passing or hanging depending on the network.

The guard also holds under ``--parallel``. macOS starts each worker as a
fresh process, which never saw the main process's patch, so until
2026-09-23 the workers could reach the internet and this runner's own test
failed there. Each worker now installs the guard as it starts, and a test
that was refused fails as itself, in whichever process ran it. A worker
also sends each failure's traceback as text: a traceback object cannot be
pickled, so one failing test used to end the whole parallel run with
"cannot pickle 'traceback' object" instead of naming the test.
"""

import socket
import traceback

from django.test.runner import (
    DiscoverRunner,
    ParallelTestSuite,
    RemoteTestResult,
    RemoteTestRunner,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}

# (test id, host) for every refused attempt, and the test running now.
_refused: list[tuple[str, str]] = []
_current_test = ["(outside a test)"]


class NetworkBlockedInTests(OSError):
    """Raised when a test tries to reach a host outside this machine."""


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    return host in _LOCAL_HOSTS or host.startswith("127.")


def _blocked(host):
    _refused.append((_current_test[0], str(host)))
    return NetworkBlockedInTests(
        f"Tests may not reach the network (tried {host!r}). Mock the service "
        "that made this call."
    )


# The real socket functions, kept while the guard is in place.
_originals: dict = {}


def install_guard(*_args):
    """Refuse connections and lookups outside this machine, in this process.

    Also the parallel workers' start-up hook, which Django calls with its
    own arguments; they are not needed here.
    """
    if _originals:
        return
    _originals.update(connect=socket.socket.connect, connect_ex=socket.socket.connect_ex,
                      getaddrinfo=socket.getaddrinfo)
    original_connect = _originals["connect"]
    original_connect_ex = _originals["connect_ex"]
    original_getaddrinfo = _originals["getaddrinfo"]

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_local(host):
            return original_connect(sock, address)
        raise _blocked(host)

    def connect_ex(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_local(host):
            return original_connect_ex(sock, address)
        raise _blocked(host)

    def getaddrinfo(host, *args, **kwargs):
        if _is_local(host):
            return original_getaddrinfo(host, *args, **kwargs)
        raise _blocked(host)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


def remove_guard():
    if not _originals:
        return
    socket.socket.connect = _originals["connect"]
    socket.socket.connect_ex = _originals["connect_ex"]
    socket.getaddrinfo = _originals["getaddrinfo"]
    _originals.clear()


class RefusalsFailTheTest:
    """Result mixin: a test that was refused fails, even when the service it
    called caught the error and carried on."""

    def startTest(self, test):
        _current_test[0] = test.id()
        super().startTest(test)

    def addSuccess(self, test):
        hosts = sorted({host for test_id, host in _refused if test_id == test.id()})
        if not hosts:
            return super().addSuccess(test)
        _refused[:] = [r for r in _refused if r[0] != test.id()]
        error = NetworkBlockedInTests(
            f"This test tried to reach the network ({', '.join(hosts)}), which tests "
            "may not do. Mock the service it calls."
        )
        # No traceback: a parallel worker has to pickle the failure.
        self.addFailure(test, (NetworkBlockedInTests, error, None))


class WorkerFailure(Exception):
    """A failure in a parallel worker, carrying its traceback as text."""


def _as_text(err):
    if err is None or err[2] is None:
        return err
    return (WorkerFailure, WorkerFailure("".join(traceback.format_exception(*err))), None)


class _WorkerResult(RefusalsFailTheTest, RemoteTestResult):
    def addError(self, test, err):
        super().addError(test, _as_text(err))

    def addFailure(self, test, err):
        super().addFailure(test, _as_text(err))

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, _as_text(err))


class _WorkerRunner(RemoteTestRunner):
    resultclass = _WorkerResult


class NoNetworkParallelTestSuite(ParallelTestSuite):
    process_setup = install_guard
    runner_class = _WorkerRunner


class NoNetworkTestRunner(DiscoverRunner):
    """DiscoverRunner with outbound connections and lookups refused."""

    parallel_test_suite = NoNetworkParallelTestSuite

    def get_resultclass(self):
        base = super().get_resultclass() or self.test_runner.resultclass

        class TracksCurrentTest(RefusalsFailTheTest, base):
            pass

        return TracksCurrentTest

    def suite_result(self, suite, result, **kwargs):
        # What is left was refused outside a test's own run (a class or
        # module set-up), so no single test could fail for it.
        failures = super().suite_result(suite, result, **kwargs)
        if not _refused:
            return failures
        seen = {}
        for test_id, host in _refused:
            seen.setdefault(test_id, set()).add(host)
        lines = [f"  {test_id}: {', '.join(sorted(hosts))}" for test_id, hosts in sorted(seen.items())]
        self.log(
            "\nThe network was reached outside a test's own run (in a class or module "
            "set-up), which tests may not do. Mock the service each one calls:\n"
            + "\n".join(lines)
        )
        return failures + len(seen)

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        install_guard()

    def teardown_test_environment(self, **kwargs):
        remove_guard()
        super().teardown_test_environment(**kwargs)
