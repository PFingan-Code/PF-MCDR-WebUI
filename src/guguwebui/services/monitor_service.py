"""
服务器状态监控服务
=================

- 后台守护线程每秒采样系统与 Minecraft 进程指标（psutil，MCDR 自带依赖）
- 1 秒精度环形缓冲：保留最近 1 小时（仅内存）
- 1 分钟均值：保留最近 7 天（SQLite 持久化到 config/guguwebui/guguwebui.sqlite3，
  插件重载 / MCDR 重启后历史数据仍保留）

TPS / MSPT 通过 MCDR RCON 优先执行原版 ``/tick query``（1.20.5+，一次查询同时给出平均
每 tick 耗时与目标 tick rate，TPS 由 ``min(target, 1000/MSPT)`` 推导）；不可用时回退
``/tps``、``/forge tps``、``/mspt`` 获取，每 5 秒查询一次并回落复用最近值。
兼容多服务端：/mspt 是原版命令（1.16+），正则同时兼容 Paper 的 ``avg`` 与原版的 ``average``
输出；TPS 依次尝试 Paper/Spigot 的 ``/tps``、Forge/NeoForge 的 ``/forge tps``，均不可用时
（原版/Fabric 无 TPS 命令）由 MSPT 推算 ``min(20, 1000/MSPT)`` 作为近似值。RCON 未启用或
服务端不支持时对应字段为 None，前端显示 N/A。其余系统指标不受影响。
"""

import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import psutil

from guguwebui.constant import DATA_DB_PATH

# 采样与保留配置
SAMPLE_INTERVAL = 1.0  # 秒级采样间隔
SECONDS_MAXLEN = 3600  # 1 秒采样保留最近 1 小时
MINUTES_MAXLEN = 7 * 24 * 60  # 1 分钟均值保留最近 7 天
TPS_QUERY_INTERVAL = 5.0  # RCON TPS/MSPT 查询节流（秒）
DISK_QUERY_INTERVAL = 10.0  # 磁盘用量查询节流（秒）

# 前端时间范围（秒）
RANGE_MAP: Dict[str, int] = {
    "10m": 10 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}

# 统一存放到样本/分钟聚合中的字段
FIELDS: Tuple[str, ...] = (
    "cpu_sys",
    "cpu_mc",
    "mem_percent",
    "mem_mc",
    "swap_percent",
    "disk_percent",
    "net_rx",
    "net_tx",
    "tps",
    "mspt",
    "load1",
    "load5",
    "load15",
)

DB_PATH = DATA_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_stats (
    ts INTEGER PRIMARY KEY,
    cpu_sys REAL, cpu_mc REAL, mem_percent REAL, mem_mc REAL,
    swap_percent REAL, disk_percent REAL, net_rx REAL, net_tx REAL,
    tps REAL, mspt REAL, load1 REAL, load5 REAL, load15 REAL
)
"""

_COLUMNS = (
    "ts",
    "cpu_sys",
    "cpu_mc",
    "mem_percent",
    "mem_mc",
    "swap_percent",
    "disk_percent",
    "net_rx",
    "net_tx",
    "tps",
    "mspt",
    "load1",
    "load5",
    "load15",
)


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _fmt_table_row(avg: Optional[float], mn: Optional[float], mx: Optional[float]) -> Dict[str, Any]:
    """统一封装统计行：avg/min/max，None 表示无数据"""
    return {"avg": avg, "min": mn, "max": mx}


class MonitorService:
    def __init__(self, server) -> None:
        self.server = server
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 1 秒环形缓冲（最近 1 小时）
        self.seconds: Deque[Dict[str, Any]] = deque(maxlen=SECONDS_MAXLEN)
        # 1 分钟均值环形缓冲（最近 7 天）
        self.minutes: Deque[Dict[str, Any]] = deque(maxlen=MINUTES_MAXLEN)

        # 采样中间状态
        self._cpu_primed = False
        self._mc_procs: Dict[int, psutil.Process] = {}
        self._net_prev: Optional[Any] = None
        self._last_sample_ts: Optional[float] = None
        self._last_disk_query_ts = 0.0
        self._disk_cache: Optional[Tuple[str, Optional[int], Optional[int], Optional[float]]] = None

        # TPS/MSPT 节流与回落
        self._last_tps_query = 0.0
        self._last_tps: Optional[float] = None
        self._last_mspt: Optional[float] = None

        # 服务器在线时长
        self._mc_was_running = False
        self._mc_started_at: Optional[float] = None

        # 分钟聚合
        self._minute_start = 0
        self._minute_acc: Dict[str, float] = {}
        self._minute_cnt: Dict[str, int] = {}
        self._minute_has_data = False

        self._load_from_db()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """启动采样线程（幂等：重复调用不会重复启动）"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="guguwebui-monitor")
        self._thread.start()

    def stop(self) -> None:
        """停止采样线程；未写完的当前分钟桶也会落库，避免数据丢失"""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        # 把尚未聚合完成的分钟桶写库
        if self._minute_has_data:
            try:
                self._finalize_minute(self._minute_start)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 采样主循环
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            tick_start = time.time()
            try:
                self._sample()
            except Exception as e:
                self._log(f"监控采样出错: {e}")
            elapsed = time.time() - tick_start
            time.sleep(max(0.0, SAMPLE_INTERVAL - elapsed))

    def _sample(self) -> None:
        now = time.time()
        running = self.server.is_server_running()

        # 服务器在线时长
        if running and not self._mc_was_running:
            self._mc_started_at = now
        if not running:
            self._mc_started_at = None
        self._mc_was_running = running

        # 整机 CPU（模块级游标，仅供本线程调用，getters 不得调用 cpu_percent）
        if self._cpu_primed:
            cpu_sys: Optional[float] = psutil.cpu_percent(interval=None)
        else:
            cpu_sys = 0.0
            self._cpu_primed = True

        # Minecraft 进程 CPU/内存（进程组：bash 包装 + java 及其子进程）
        cpu_mc, mem_mc = self._sample_mc(running)

        # 内存 / Swap
        try:
            vm = psutil.virtual_memory()
            mem_percent: Optional[float] = vm.percent
        except Exception:
            vm = None
            mem_percent = None
        try:
            sw = psutil.swap_memory()
            swap_percent: Optional[float] = sw.percent
        except Exception:
            swap_percent = None

        # 磁盘（节流，10 秒一次）
        if now - self._last_disk_query_ts >= DISK_QUERY_INTERVAL:
            self._disk_cache = self._query_disk_usage()
            self._last_disk_query_ts = now
        disk_percent = self._disk_cache[3] if self._disk_cache else None

        # 网络速率（两次 io_counters 差值 / 距上次采样时间）
        net_rx = net_tx = 0.0
        try:
            net = psutil.net_io_counters()
            if self._net_prev is not None and self._last_sample_ts is not None:
                dt = max(now - self._last_sample_ts, 1e-6)
                net_rx = max(0.0, (net.bytes_recv - self._net_prev.bytes_recv)) / dt
                net_tx = max(0.0, (net.bytes_sent - self._net_prev.bytes_sent)) / dt
            self._net_prev = net
        except Exception:
            net_rx = net_tx = 0.0

        # 系统负载（Linux/macOS；Windows 为 None）
        load1 = load5 = load15 = None
        try:
            load1, load5, load15 = psutil.getloadavg()
        except Exception:
            pass

        # TPS/MSPT（RCON，5 秒节流，回落复用最近值；优先 /tick query，其次 /mspt + /tps）
        tps = mspt = None
        if running:
            if now - self._last_tps_query >= TPS_QUERY_INTERVAL:
                self._last_tps_query = now
                tps_q, mspt_q = self._query_tps_mspt()
                if tps_q is not None:
                    self._last_tps = tps_q
                if mspt_q is not None:
                    self._last_mspt = mspt_q
            tps = self._last_tps
            mspt = self._last_mspt
        else:
            self._last_tps = None
            self._last_mspt = None

        sample: Dict[str, Any] = {
            "ts": now,
            "cpu_sys": round(cpu_sys, 2) if cpu_sys is not None else None,
            "cpu_mc": round(cpu_mc, 2) if cpu_mc is not None else None,
            "mem_percent": mem_percent,
            "mem_mc": mem_mc,
            "swap_percent": swap_percent,
            "disk_percent": disk_percent,
            "net_rx": round(net_rx, 2),
            "net_tx": round(net_tx, 2),
            "tps": tps,
            "mspt": mspt,
            "load1": load1,
            "load5": load5,
            "load15": load15,
        }
        self.seconds.append(sample)
        self._last_sample_ts = now
        self._accumulate_minute(now, sample)

    def _sample_mc(self, running: bool) -> Tuple[Optional[float], Optional[int]]:
        if not running:
            self._mc_procs.clear()
            return None, None

        current_pids = set()
        try:
            current_pids = set(self.server.get_server_pid_all())
        except Exception:
            current_pids = set()

        # 清理已退出的进程
        for pid in list(self._mc_procs.keys()):
            if pid not in current_pids:
                del self._mc_procs[pid]

        # 为新 PID 创建 Process 实例（cpu_percent 依赖实例内缓存，必须复用实例）
        for pid in current_pids:
            if pid not in self._mc_procs:
                try:
                    self._mc_procs[pid] = psutil.Process(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

        total_cpu = 0.0
        total_mem = 0
        for pid, proc in list(self._mc_procs.items()):
            try:
                cpu = proc.cpu_percent(interval=None)
                if cpu is not None:
                    total_cpu += max(0.0, cpu)
                mem = proc.memory_info().rss
                total_mem += mem
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                del self._mc_procs[pid]
            except Exception:
                continue

        if not self._mc_procs:
            return None, None

        # 归一化为「占整机总 CPU 的百分比」，与整机曲线同量纲
        try:
            cpu_count = psutil.cpu_count() or 1
        except Exception:
            cpu_count = 1
        cpu_mc = total_cpu / cpu_count
        return cpu_mc, total_mem

    # ------------------------------------------------------------------ #
    # RCON TPS / MSPT
    # ------------------------------------------------------------------ #
    def _query_tps(self, mspt: Optional[float] = None) -> Optional[float]:
        """查询 TPS，按服务端类型依次回落：

        1. Paper/Spigot 的 ``/tps``（"TPS from last 1m, 5m, 15m: ..."）
        2. Forge/NeoForge 加载器内置的 ``/forge tps``（"Overall TPS: ..."，无需装模组）
        3. 两者都没有（原版/Fabric）：由 MSPT 推算 ``min(20, 1000/MSPT)`` 近似值
        """
        # 1) Paper/Spigot
        try:
            resp = self.server.rcon_query("/tps")
            if resp:
                m = re.search(r"TPS from last\s+1m,\s*5m,\s*15m:\s*([\d.]+)", resp)
                if m:
                    return float(m.group(1))
        except Exception:
            pass

        # 2) Forge/NeoForge
        try:
            resp = self.server.rcon_query("/forge tps")
            if resp:
                m = re.search(r"Overall\s+TPS:\s*([\d.]+)", resp)
                if m:
                    return float(m.group(1))
        except Exception:
            pass

        # 3) 由 MSPT 推算（近似值；tick 循环被 GC 等外部阻塞时可能高估）
        if mspt is not None and mspt > 0:
            return round(min(20.0, 1000.0 / mspt), 2)
        return None

    def _query_mspt(self) -> Optional[float]:
        try:
            resp = self.server.rcon_query("/mspt")
            if not resp:
                return None
            # Paper: "2.0ms avg"; 原版 1.16+: "1.85ms average"
            m = re.search(r"([\d.]+)ms\s+(?:avg|average)", resp)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def _query_tick_query(self) -> Optional[Tuple[float, float]]:
        """执行原版 ``/tick query``（1.20.5+，Vanilla/Fabric 等均可用），
        返回 ``(平均每 tick 毫秒, 目标 tick rate)``，解析失败返回 None。"""
        try:
            resp = self.server.rcon_query("/tick query")
            if not resp:
                return None
            avg_m = re.search(r"Average\s+time\s+per\s+tick:\s*([\d.]+)ms", resp)
            if not avg_m:
                return None
            target_m = re.search(
                r"Target\s+tick\s+rate:\s*([\d.]+)\s+per\s+second", resp
            )
            target = float(target_m.group(1)) if target_m else 20.0
            return float(avg_m.group(1)), target
        except Exception:
            return None

    def _query_tps_mspt(self) -> Tuple[Optional[float], Optional[float]]:
        """查询 TPS / MSPT，优先使用原版 ``/tick query``（一次查询同时给出平均
        每 tick 耗时与目标 tick rate，TPS 由 ``min(target, 1000/MSPT)`` 推导）；
        不可用时回退 ``/mspt`` + ``/tps`` / ``/forge tps``，最后用 MSPT 推算 TPS。"""
        tick = self._query_tick_query()
        if tick is not None:
            avg_ms, target = tick
            mspt = round(avg_ms, 2)
            tps = round(min(target, 1000.0 / avg_ms), 2) if avg_ms > 0 else target
            return tps, mspt

        mspt = self._query_mspt()
        tps = self._query_tps(mspt)
        return tps, mspt

    # ------------------------------------------------------------------ #
    # 磁盘
    # ------------------------------------------------------------------ #
    def _query_disk_usage(self) -> Tuple[str, Optional[int], Optional[int], Optional[float]]:
        working_dir = "server"
        try:
            mcdr_config = self.server.get_mcdr_config()
            if isinstance(mcdr_config, dict):
                working_dir = str(mcdr_config.get("working_directory") or "server")
        except Exception:
            pass
        try:
            du = psutil.disk_usage(working_dir)
            return working_dir, du.total, du.used, du.percent
        except Exception:
            return working_dir, None, None, None

    # ------------------------------------------------------------------ #
    # 分钟聚合与 SQLite
    # ------------------------------------------------------------------ #
    def _accumulate_minute(self, now: float, sample: Dict[str, Any]) -> None:
        minute_start = int(now // 60) * 60
        if minute_start != self._minute_start:
            if self._minute_has_data:
                self._finalize_minute(self._minute_start)
            self._minute_start = minute_start
            self._minute_acc = {f: 0.0 for f in FIELDS}
            self._minute_cnt = {f: 0 for f in FIELDS}
            self._minute_has_data = False

        for f in FIELDS:
            v = sample.get(f)
            if v is not None and isinstance(v, (int, float)):
                self._minute_acc[f] += v
                self._minute_cnt[f] += 1
                self._minute_has_data = True

    def _finalize_minute(self, ts: int) -> None:
        row: Dict[str, Any] = {"ts": ts}
        for f in FIELDS:
            cnt = self._minute_cnt.get(f, 0)
            row[f] = self._minute_acc[f] / cnt if cnt else None
        self.minutes.append(row)
        self._write_db(row)
        self._prune_db()
        self._minute_has_data = False

    def _db_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(_SCHEMA)
        return conn

    def _write_db(self, row: Dict[str, Any]) -> None:
        try:
            conn = self._db_connect()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO minute_stats ({','.join(_COLUMNS)}) "
                    f"VALUES ({','.join(['?'] * len(_COLUMNS))})",
                    [row.get(c) for c in _COLUMNS],
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _prune_db(self) -> None:
        """删除 7 天前的分钟记录"""
        try:
            cutoff = int(time.time()) - MINUTES_MAXLEN * 60
            conn = self._db_connect()
            try:
                conn.execute("DELETE FROM minute_stats WHERE ts < ?", (cutoff,))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _load_from_db(self) -> None:
        """启动时回载最近 7 天的分钟数据"""
        try:
            if not DB_PATH.exists():
                return
            cutoff = int(time.time()) - MINUTES_MAXLEN * 60
            conn = sqlite3.connect(str(DB_PATH))
            try:
                conn.execute(_SCHEMA)
                rows = conn.execute(
                    f"SELECT {','.join(_COLUMNS)} FROM minute_stats WHERE ts >= ? ORDER BY ts",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    self.minutes.append(dict(zip(_COLUMNS, row)))
            finally:
                conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 对外查询接口
    # ------------------------------------------------------------------ #
    def get_overview(self) -> Dict[str, Any]:
        """最新状态快照（绝对值）。注意：不得在此调用 psutil.cpu_percent / Process.cpu_percent，
        否则会消费采样子线程的模块级游标，导致 CPU 曲线失真；CPU 值取最近一次采样。"""
        latest = self.seconds[-1] if self.seconds else None

        online = self.server.is_server_running()
        uptime = None
        if online and self._mc_started_at is not None:
            uptime = max(0, int(time.time() - self._mc_started_at))

        try:
            vm = psutil.virtual_memory()
            mem_total, mem_used, mem_percent = vm.total, vm.used, vm.percent
            mem_available = vm.available
        except Exception:
            mem_total = mem_used = mem_available = mem_percent = None

        try:
            sw = psutil.swap_memory()
            swap_total, swap_used, swap_percent = sw.total, sw.used, sw.percent
        except Exception:
            swap_total = swap_used = swap_percent = None

        load1 = load5 = load15 = None
        try:
            load1, load5, load15 = psutil.getloadavg()
        except Exception:
            pass

        disk_path, disk_total, disk_used, disk_percent = self._disk_cache or ("server", None, None, None)

        return {
            "ts": int(time.time()),
            "online": online,
            "uptime": uptime,
            "tps": latest.get("tps") if latest else None,
            "mspt": latest.get("mspt") if latest else None,
            "cpu": {
                "system": latest.get("cpu_sys") if latest else None,
                "minecraft": latest.get("cpu_mc") if latest else None,
            },
            "memory": {
                "total": mem_total,
                "available": mem_available,
                "used": mem_used,
                "percent": mem_percent,
                "minecraft": latest.get("mem_mc") if latest else None,
                "swap_total": swap_total,
                "swap_used": swap_used,
                "swap_percent": swap_percent,
            },
            "disk": {
                "path": disk_path,
                "total": disk_total,
                "used": disk_used,
                "percent": disk_percent,
            },
            "load": {"load1": load1, "load5": load5, "load15": load15},
            "network": {
                "rx": latest.get("net_rx") if latest else None,
                "tx": latest.get("net_tx") if latest else None,
            },
        }

    def get_history(self, metric: str, range_key: str) -> Dict[str, Any]:
        """按指标与时间范围返回时间序列；服务端降采样到不超过 MAX_POINTS 点。

        metric 支持: cpu / memory / network / tps / mspt / load / disk
        """
        range_seconds = RANGE_MAP.get(range_key, RANGE_MAP["1h"])
        # 范围 ≤ 1h 使用 1 秒采样；更大范围使用 1 分钟均值
        use_minutes = range_seconds > RANGE_MAP["1h"]
        data = self.minutes if use_minutes else self.seconds

        cutoff = time.time() - range_seconds
        points = [s for s in data if s.get("ts", 0) >= cutoff]

        # 降采样：相邻桶取均值，控制返回点数
        points = self._downsample(points)

        result_points: List[Dict[str, Any]] = []
        for p in points:
            entry: Dict[str, Any] = {"t": int(p["ts"])}
            if metric == "cpu":
                entry["system"] = p.get("cpu_sys")
                entry["minecraft"] = p.get("cpu_mc")
            elif metric == "memory":
                entry["system"] = p.get("mem_percent")
                # 进程内存转换为「占整机总内存的百分比」，与整机曲线同量纲
                mc_mem = p.get("mem_mc")
                mem_total = self._current_mem_total()
                if mc_mem is not None and mem_total:
                    entry["minecraft"] = round(mc_mem / mem_total * 100, 2)
                else:
                    entry["minecraft"] = None
            elif metric == "network":
                entry["rx"] = p.get("net_rx")
                entry["tx"] = p.get("net_tx")
            elif metric == "tps":
                entry["value"] = p.get("tps")
            elif metric == "mspt":
                entry["value"] = p.get("mspt")
            elif metric == "load":
                entry["value"] = p.get("load1")
            elif metric == "disk":
                entry["value"] = p.get("disk_percent")
            else:
                raise ValueError(f"Unknown metric: {metric}")
            result_points.append(entry)

        # service 不再自造外壳（status 键），统一由路由层包 envelope
        return {
            "metric": metric,
            "range": range_key,
            "sample": "1s" if not use_minutes else "1m",
            "points": result_points,
        }

    def get_table(self, range_key: str) -> Dict[str, Any]:
        """统计表数据：各指标在当前时间范围内的 avg/min/max（分钟聚合口径）"""
        range_seconds = RANGE_MAP.get(range_key, RANGE_MAP["1h"])
        use_minutes = range_seconds > RANGE_MAP["1h"]
        data = self.minutes if use_minutes else self.seconds

        cutoff = time.time() - range_seconds
        points = [s for s in data if s.get("ts", 0) >= cutoff]

        def stat(field: str) -> Dict[str, Any]:
            values = [p[field] for p in points if p.get(field) is not None]
            if not values:
                return _fmt_table_row(None, None, None)
            return _fmt_table_row(
                round(sum(values) / len(values), 2),
                round(min(values), 2),
                round(max(values), 2),
            )

        # service 不再自造外壳（status 键），统一由路由层包 envelope
        return {
            "range": range_key,
            "stats": {
                "tps": stat("tps"),
                "mspt": stat("mspt"),
                "cpu": {"system": stat("cpu_sys"), "minecraft": stat("cpu_mc")},
                # 进程内存统计保留字节数，表格按 GB 展示；折线图另在 get_history 中按整机总内存归一化为百分比
                "memory": {"system": stat("mem_percent"), "minecraft": stat("mem_mc")},
                "swap": stat("swap_percent"),
                "disk": stat("disk_percent"),
                "load": {
                    "load1": stat("load1"),
                    "load5": stat("load5"),
                    "load15": stat("load15"),
                },
                "network": {"rx": stat("net_rx"), "tx": stat("net_tx")},
            },
        }

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _downsample(points: List[Dict[str, Any]], max_points: int = 1500) -> List[Dict[str, Any]]:
        if len(points) <= max_points:
            return points
        bucket = len(points) / max_points
        result: List[Dict[str, Any]] = []
        idx = 0.0
        while idx < len(points):
            start = int(idx)
            end = min(int(idx + bucket), len(points))
            chunk = points[start:end]
            if not chunk:
                break
            agg: Dict[str, Any] = {"ts": chunk[0]["ts"]}
            # 对数值字段取均值
            numeric_fields = {
                k for k in chunk[0].keys() if k != "ts" and isinstance(chunk[0][k], (int, float))
            }
            for f in numeric_fields:
                vals = [c[f] for c in chunk if c.get(f) is not None]
                agg[f] = round(sum(vals) / len(vals), 2) if vals else None
            result.append(agg)
            idx += bucket
        return result

    def _current_mem_total(self) -> Optional[int]:
        try:
            return psutil.virtual_memory().total
        except Exception:
            return None

    def _log(self, message: str) -> None:
        try:
            self.server.logger.warning(message)
        except Exception:
            pass