"""Operator settings that must survive a restart.

Real gear does not forget how it was configured. An APC NMC writes its trap
receivers to NVRAM; a Cisco switch keeps `snmp-server host` in the startup
config. Reboot the device and the traps still go where the operator pointed
them. Anything this simulator models as *device configuration* — as opposed to
live measurement — therefore has to outlive the process.

This is a deliberately small file-backed store, not a config framework: a flat
JSON document at the repository root, written atomically, and read back with a
default for every key. It is NOT the place for topology, datasets or bindings —
each of those already owns its own on-disk representation (see
core.dataset_fingerprint for the reconcile rules).

Writes are best-effort. A simulator that cannot save a preference must still
run, so a failed write is logged and swallowed rather than raised into the
caller's control flow.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

# Resolved from this module, not from the process working directory: the API can
# be started from anywhere and must still find the same file. DCIM_SIM_SETTINGS
# redirects it, which is what a test or a CI run should do rather than writing
# into the developer's own install — configure() saves on every call, so a
# harness that points a TrapEngine somewhere would otherwise overwrite the real
# receiver.
SETTINGS_FILE = Path(
    os.environ.get("DCIM_SIM_SETTINGS")
    or Path(__file__).resolve().parent.parent / "sim_settings.json"
)

_lock = threading.Lock()
_cache: Dict[str, Any] | None = None


def _read() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    data: Dict[str, Any] = {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
        else:
            log.warning("[Settings] %s is not a JSON object — ignoring it",
                        SETTINGS_FILE)
    except FileNotFoundError:
        pass                      # first run — defaults apply
    except (json.JSONDecodeError, OSError) as ex:
        # A corrupt settings file must not stop the simulator booting.
        log.warning("[Settings] could not read %s (%s) — using defaults",
                    SETTINGS_FILE, ex)
    _cache = data
    return data


def get(key: str, default: Any = None) -> Any:
    """Read one setting, falling back to `default` when it was never saved."""
    with _lock:
        return _read().get(key, default)


def set_many(values: Dict[str, Any]) -> bool:
    """Merge `values` into the stored settings. Returns False if the save failed.

    Written to a temporary file and renamed so a crash mid-write cannot leave a
    truncated document behind — the same reason the dataset fingerprint is
    written that way.
    """
    with _lock:
        data = dict(_read())
        data.update(values)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, SETTINGS_FILE)
        except OSError as ex:
            log.warning("[Settings] could not write %s (%s) — the change applies "
                        "to this session only", SETTINGS_FILE, ex)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        global _cache
        _cache = data
        return True


def set(key: str, value: Any) -> bool:
    """Write one setting. Returns False if the save failed."""
    return set_many({key: value})
