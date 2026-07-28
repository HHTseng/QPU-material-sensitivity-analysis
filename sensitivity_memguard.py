"""RSS-based memory guard for stage-1 Geant4/G4CMP batches.

mimir is a shared 503 GB box with other users' jobs on it, so a runaway sample
must never be allowed to consume the machine. G4CMP has a measured runaway mode
(G4CMP_crash_and_memory_analysis.md, Finding 1: the KaplanQP infinite loop,
observed at 28 GB -> 79 GB RSS and still climbing), so this is a real risk, not
a hypothetical one.

Why RSS and not resource.setrlimit(RLIMIT_AS):
    RLIMIT_AS caps *virtual* address space. A healthy Main here is ~70 MB RSS
    but reserves far more VA, and Geant4/CLHEP make large speculative
    reservations at startup, so any RLIMIT_AS low enough to stop a runaway
    promptly also risks killing healthy runs at initialization. RSS is what
    actually occupies machine memory, which is the quantity we owe the other
    users on this host. So we poll RSS and kill, rather than capping VA.

Two independent limits, both enforced here:
    per-sample  -- one sample's process tree exceeding PER_SAMPLE_GB is a
                   runaway by definition (healthy is ~0.07 GB); kill just it.
    aggregate   -- total RSS across all sample process trees must stay under
                   TOTAL_GB; if it does not, kill the largest offender first
                   and keep killing until back under budget.

Killing targets the whole process group. Killing the bash wrapper alone leaves
an orphaned `Main` spinning at 100% CPU (same finding as above).
"""

import os
import signal
import threading
import time

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def _rss_bytes(pid):
    """RSS of one pid, from /proc/<pid>/statm. 0 if the pid is gone."""
    try:
        with open(f"/proc/{pid}/statm", "rb") as handle:
            return int(handle.read().split()[1]) * PAGE_SIZE
    except (OSError, ValueError, IndexError):
        return 0


def _descendants(root_pid):
    """root_pid plus every descendant, via /proc/<pid>/task/*/children.

    The sample is launched as `bash -lc ... Main`, so the process that actually
    grows is a grandchild of the pid we hold; walking the tree is required.
    """
    seen = [root_pid]
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        try:
            task_dir = f"/proc/{pid}/task"
            for tid in os.listdir(task_dir):
                try:
                    with open(f"{task_dir}/{tid}/children", "rb") as handle:
                        kids = [int(t) for t in handle.read().split()]
                except OSError:
                    continue
                for kid in kids:
                    if kid not in seen:
                        seen.append(kid)
                        frontier.append(kid)
        except OSError:
            continue
    return seen


def tree_rss_bytes(root_pid):
    return sum(_rss_bytes(pid) for pid in _descendants(root_pid))


def kill_group(pid):
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        return True
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except (ProcessLookupError, PermissionError):
            return False


class MemoryGuard:
    """Polls registered sample process trees and enforces both limits.

    Registration is by pid. Workers register their subprocess as soon as it is
    spawned and unregister when it exits; the guard thread is the only thing
    that kills on memory grounds.
    """

    def __init__(self, total_gb, per_sample_gb, poll_seconds=5.0, on_kill=None):
        self.total_bytes = int(total_gb * 1024 ** 3)
        self.per_sample_bytes = int(per_sample_gb * 1024 ** 3)
        self.poll_seconds = poll_seconds
        self.on_kill = on_kill
        self._pids = {}  # pid -> label
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.killed = []
        self.peak_total_bytes = 0

    def register(self, pid, label):
        with self._lock:
            self._pids[pid] = label

    def unregister(self, pid):
        with self._lock:
            self._pids.pop(pid, None)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_seconds * 2)

    def _report(self, label, pid, rss, reason):
        self.killed.append((label, rss, reason))
        print(
            f"MEMGUARD: killed {label} (pid {pid}, RSS {rss / 1024 ** 3:.2f} GB): {reason}",
            flush=True,
        )
        if self.on_kill is not None:
            self.on_kill(label, pid, rss, reason)

    def _loop(self):
        while not self._stop.wait(self.poll_seconds):
            with self._lock:
                snapshot = list(self._pids.items())
            if not snapshot:
                continue

            usage = []
            for pid, label in snapshot:
                usage.append((tree_rss_bytes(pid), pid, label))

            total = sum(item[0] for item in usage)
            self.peak_total_bytes = max(self.peak_total_bytes, total)

            # Per-sample limit: a single tree over the cap is a runaway.
            for rss, pid, label in usage:
                if rss > self.per_sample_bytes:
                    if kill_group(pid):
                        self._report(
                            label, pid, rss,
                            f"per-sample RSS cap {self.per_sample_bytes / 1024 ** 3:.1f} GB exceeded",
                        )
                    self.unregister(pid)

            # Aggregate limit: kill largest-first until back under budget.
            if total > self.total_bytes:
                for rss, pid, label in sorted(usage, reverse=True):
                    if total <= self.total_bytes:
                        break
                    if kill_group(pid):
                        self._report(
                            label, pid, rss,
                            f"aggregate RSS budget {self.total_bytes / 1024 ** 3:.0f} GB exceeded "
                            f"(total was {total / 1024 ** 3:.1f} GB)",
                        )
                        total -= rss
                    self.unregister(pid)


def host_available_gb():
    """MemAvailable from /proc/meminfo, in GB."""
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 ** 2
    except OSError:
        pass
    return float("nan")
