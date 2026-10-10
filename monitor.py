"""Background GPU / RAM sampler.

VRAM is attributed to the model in this order of preference:
  1. per-process GPU memory of the server processes (models.yaml `process_match`, or a launched pid)
  2. server-reported size (Ollama /api/ps)
  3. device-wide used memory minus the baseline measured before the model was loaded
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Any

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    _NVML = True
except Exception:  # noqa: BLE001
    _NVML = False


def gpu_inventory() -> list[dict[str, Any]]:
    if not _NVML:
        return []
    out = []
    for i in range(pynvml.nvmlDeviceGetCount()):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        name = pynvml.nvmlDeviceGetName(h)
        mem_mb = None
        try:
            mem_mb = pynvml.nvmlDeviceGetMemoryInfo(h).total / 2**20
        except Exception:
            pass
        out.append({"index": i, "name": name.decode() if isinstance(name, bytes) else name,
                    "memory_total_mb": mem_mb})
    return out


def system_info() -> dict[str, Any]:
    info: dict[str, Any] = {"gpus": gpu_inventory()}
    if _NVML:
        v = pynvml.nvmlSystemGetDriverVersion()
        info["driver_version"] = v.decode() if isinstance(v, bytes) else v
        cv = pynvml.nvmlSystemGetCudaDriverVersion()
        info["cuda_driver_api"] = f"{cv // 1000}.{(cv % 1000) // 10}"
    try:
        out = subprocess.run(["nvcc", "--version"], capture_output=True, text=True, timeout=5).stdout
        info["nvcc"] = out.strip().splitlines()[-1] if out else None
    except (OSError, subprocess.SubprocessError):
        info["nvcc"] = None
    info["cpu"] = psutil.cpu_count()
    info["ram_total_gb"] = psutil.virtual_memory().total / 2**30
    return info


def _matching_pids(patterns: list[str], extra_pids: list[int]) -> set[int]:
    pids = set()
    for p in extra_pids:
        try:
            proc = psutil.Process(p)
            pids.add(p)
            pids |= {c.pid for c in proc.children(recursive=True)}
        except psutil.Error:
            pass
    if patterns:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                hay = (proc.info["name"] or "") + " " + " ".join(proc.info["cmdline"] or [])
            except psutil.Error:
                continue
            if any(pat in hay for pat in patterns):
                pids.add(proc.info["pid"])
    return pids


class ResourceMonitor:
    def __init__(self, gpu_indices: list[int] | None = None, interval: float = 0.2,
                 process_match: list[str] | None = None, pids: list[int] | None = None):
        self.interval = interval
        self.process_match = process_match or []
        self.pids = pids or []
        self.handles = []
        if _NVML:
            idx = gpu_indices if gpu_indices is not None else range(pynvml.nvmlDeviceGetCount())
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in idx]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.baseline_mb = self.device_used_mb()
        self.reset()

    def reset(self) -> None:
        self.dev_peak_mb = 0.0
        self.proc_gpu_peak_mb = 0.0
        self.rss_peak_mb = 0.0
        self.util: list[float] = []

    def device_used_mb(self) -> float:
        total = 0.0
        for h in self.handles:
            try:
                total += pynvml.nvmlDeviceGetMemoryInfo(h).used / 2**20
            except Exception:
                pass
        return total

    def _sample(self) -> None:
        pids = _matching_pids(self.process_match, self.pids)
        self.dev_peak_mb = max(self.dev_peak_mb, self.device_used_mb())
        proc_gpu = 0.0
        for h in self.handles:
            try:
                for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h):
                    if p.pid in pids and p.usedGpuMemory:
                        proc_gpu += p.usedGpuMemory / 2**20
            except pynvml.NVMLError:
                pass
            try:
                self.util.append(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
            except pynvml.NVMLError:
                pass
        self.proc_gpu_peak_mb = max(self.proc_gpu_peak_mb, proc_gpu)
        rss = 0.0
        for pid in pids:
            try:
                rss += psutil.Process(pid).memory_info().rss / 2**20
            except psutil.Error:
                pass
        self.rss_peak_mb = max(self.rss_peak_mb, rss)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.interval)

    def start(self) -> "ResourceMonitor":
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def summary(self, server_info: dict[str, Any] | None = None) -> dict[str, Any]:
        server_info = server_info or {}
        out: dict[str, Any] = {
            "device_peak_mb": self.dev_peak_mb, "device_baseline_mb": self.baseline_mb,
            "device_delta_mb": max(0.0, self.dev_peak_mb - self.baseline_mb),
            "process_gpu_peak_mb": self.proc_gpu_peak_mb or None, "ram_peak_mb": self.rss_peak_mb or None,
            "gpu_util_mean": float(sum(self.util) / len(self.util)) if self.util else None,
            "gpu_util_max": max(self.util) if self.util else None, **server_info,
        }
        if self.proc_gpu_peak_mb:
            out["vram_peak_mb"], out["vram_method"] = self.proc_gpu_peak_mb, "process"
        elif server_info.get("server_vram_mb"):
            out["vram_peak_mb"], out["vram_method"] = server_info["server_vram_mb"], "server_reported"
        elif out["device_delta_mb"] > 0:
            out["vram_peak_mb"], out["vram_method"] = out["device_delta_mb"], "device_delta"
        if not out["ram_peak_mb"]:
            out["ram_peak_mb"] = psutil.virtual_memory().used / 2**20
            out["ram_method"] = "system_used"
        else:
            out["ram_method"] = "process_rss"
        return out
