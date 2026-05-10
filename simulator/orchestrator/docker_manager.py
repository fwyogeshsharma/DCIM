"""
Docker SDK wrapper — manages network-sim containers at runtime.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import docker
from docker.models.containers import Container

log = logging.getLogger("docker_manager")


class DockerManager:
    def __init__(self):
        self._client = docker.from_env()

    # ── Container introspection ───────────────────────────────────────────────

    def list_sim_containers(self, label: str = "role=network-sim") -> List[Container]:
        try:
            return self._client.containers.list(filters={"label": label})
        except Exception as e:
            log.warning("list containers error: %s", e)
            return []

    def get_container(self, name: str) -> Optional[Container]:
        try:
            return self._client.containers.get(name)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            log.warning("get container %s error: %s", name, e)
            return None

    def container_status(self, name: str) -> str:
        c = self.get_container(name)
        return c.status if c else "not_found"

    def all_containers_status(self) -> Dict[str, str]:
        try:
            containers = self._client.containers.list(all=True)
            return {c.name: c.status for c in containers}
        except Exception as e:
            log.warning("all_containers_status error: %s", e)
            return {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def restart_container(self, name: str) -> bool:
        c = self.get_container(name)
        if not c:
            log.warning("restart: container %s not found", name)
            return False
        try:
            c.restart(timeout=10)
            log.info("Restarted container: %s", name)
            return True
        except Exception as e:
            log.warning("restart %s error: %s", name, e)
            return False

    def stop_container(self, name: str, timeout: int = 10) -> bool:
        c = self.get_container(name)
        if not c:
            return False
        try:
            c.stop(timeout=timeout)
            return True
        except Exception as e:
            log.warning("stop %s error: %s", name, e)
            return False

    def start_container(self, name: str) -> bool:
        c = self.get_container(name)
        if not c:
            return False
        try:
            c.start()
            return True
        except Exception as e:
            log.warning("start %s error: %s", name, e)
            return False

    # ── Logs ─────────────────────────────────────────────────────────────────

    def get_logs(self, name: str, tail: int = 100) -> str:
        c = self.get_container(name)
        if not c:
            return ""
        try:
            return c.logs(tail=tail).decode("utf-8", errors="replace")
        except Exception as e:
            return f"error: {e}"

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self, name: str) -> Optional[dict]:
        c = self.get_container(name)
        if not c:
            return None
        try:
            raw = c.stats(stream=False)
            cpu_delta = (
                raw["cpu_stats"]["cpu_usage"]["total_usage"]
                - raw["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            sys_delta = (
                raw["cpu_stats"]["system_cpu_usage"]
                - raw["precpu_stats"]["system_cpu_usage"]
            )
            num_cpus  = raw["cpu_stats"].get("online_cpus", 1) or 1
            cpu_pct   = (cpu_delta / sys_delta) * num_cpus * 100 if sys_delta else 0

            mem_usage = raw["memory_stats"].get("usage", 0)
            mem_limit = raw["memory_stats"].get("limit", 1) or 1

            return {
                "cpu_pct":      round(cpu_pct, 2),
                "mem_usage_mb": round(mem_usage / 1e6, 1),
                "mem_limit_mb": round(mem_limit / 1e6, 1),
                "mem_pct":      round(mem_usage / mem_limit * 100, 2),
            }
        except Exception as e:
            log.debug("stats %s error: %s", name, e)
            return None
