"""Process file-descriptor limit — headroom for large in-memory fleets.

Every commissioned Redfish BMC is one ThreadingHTTPServer socket and every gNMI
target is a gRPC server (several eventfds), so the process open-fd count scales
~linearly with fleet size. The Linux default soft limit (often 1024) is
exhausted well before a few-thousand-device fleet, after which the API server's
own ``socket.accept()`` on port 8001 fails with
``OSError: [Errno 24] Too many open files`` and the process aborts. Raise the
soft limit toward the hard limit at startup so the fleet can grow without
starving the listener.

POSIX only — Windows does not use RLIMIT_NOFILE (its socket ceiling is governed
differently and this failure mode does not occur), so this is a no-op there.

NOTE: raising the fd limit only buys headroom — the real ceiling is the
per-device-socket model itself. The scalable fix is a single wildcard listener
that demuxes by destination IP (getsockname), exactly as snmpsim already does
here. Track that as the follow-up when the fleet must reach true hyperscale
counts.
"""
from __future__ import annotations

import logging

log = logging.getLogger("resource")

# Aim for this many fds. Comfortably covers a few-thousand-device fleet
# (~1 fd per Redfish BMC + ~10 fds per gNMI leaf, plus base app sockets) with
# room to spare; clamped down to the OS hard limit when that is lower.
_TARGET_FDS = 65536


def raise_fd_limit(target: int = _TARGET_FDS) -> None:
    """Best-effort raise of the RLIMIT_NOFILE soft limit toward *target*.

    No-op on non-POSIX platforms and never raises — startup must not depend on
    it. Lifting the hard limit needs privilege; if denied we still raise the
    soft limit as high as the current hard limit allows.
    """
    try:
        import resource
    except ImportError:
        return  # Windows / non-POSIX — RLIMIT_NOFILE does not apply
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target
        # If the desired ceiling exceeds the hard limit, try to lift the hard
        # limit too (needs privilege; silently ignored when denied).
        if hard != resource.RLIM_INFINITY and want > hard:
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (want, want))
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            except (ValueError, OSError):
                pass
        new_soft = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if new_soft > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
            log.info("RLIMIT_NOFILE raised %s -> %s (hard=%s)", soft, new_soft,
                     "inf" if hard == resource.RLIM_INFINITY else hard)
    except Exception as e:  # never block startup on a resource-tuning failure
        log.warning("could not raise fd limit: %s", e)
