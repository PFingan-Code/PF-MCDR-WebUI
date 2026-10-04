"""
玩家管理服务。

- 汇总所有进过本服的玩家（usercache.json + playerdata + WebUI 自身会话记录）
- 假人识别：真实玩家有 IP 记录，Carpet 假人没有 IP（与 player_ip_logger 判定一致）
- 白名单 / OP / 封禁管理：读取服务端 json 文件，动作通过 RCON 或 MCDR 转发执行
- 解封通过直接修改 banned-*.json 文件完成（需要重启服务器生效）
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import javaproperties

from guguwebui.constant import PLAYER_STATS_PATH, PLAYER_STATS_SESSION_RETENTION_DAYS
from guguwebui.utils.storage import get_state, set_state
from guguwebui.services.monitor_service import RANGE_MAP
from guguwebui.utils.api_cache import api_cache
from guguwebui.utils.mc_util import format_uuid, get_minecraft_path
from guguwebui.utils.nbt_util import read_playerdata

# 内存中的玩家上线时间（会话时长统计）
_JOIN_TIMES: Dict[str, float] = {}
_STATS_LOCK = threading.Lock()

_POS_RE = re.compile(r"\[([^\]]*)\]")
_DIM_RE = re.compile(r'"([^"]+)"')

_DEFAULT_STATS: Dict[str, Any] = {"players": {}, "sessions": []}


def _parse_pos_feedback(feedback: Optional[str]) -> Optional[Dict[str, float]]:
    """解析 `data get entity <name> Pos` 的反馈，如 `[... [12.0d, 64.0d, -34.0d]]`。"""
    if not feedback:
        return None
    m = _POS_RE.search(feedback)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
    if len(parts) < 3:
        return None
    coords = []
    for part in parts[:3]:
        try:
            coords.append(round(float(part.rstrip("dDfF")), 2))
        except ValueError:
            return None
    return {"x": coords[0], "y": coords[1], "z": coords[2]}


def _parse_dimension_feedback(feedback: Optional[str]) -> Optional[str]:
    """解析 `data get entity <name> Dimension` 的反馈。"""
    if not feedback:
        return None
    m = _DIM_RE.search(feedback)
    if m:
        return m.group(1)
    return None


def _parse_expires_on(expires_on: Optional[str]) -> Optional[float]:
    """usercache expiresOn 解析为时间戳（约 30 天前为最后在线时间）。"""
    if not expires_on:
        return None
    text = str(expires_on).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt.timestamp() - 30 * 86400
        except ValueError:
            continue
    return None


class PlayerService:
    def __init__(self, server, config_service=None):
        self.server = server
        self.config_service = config_service

    # ------------------------------------------------------------------ #
    # 路径与文件读取
    # ------------------------------------------------------------------ #

    def _working_dir(self) -> Path:
        return Path(get_minecraft_path(self.server, "working_directory"))

    def _server_properties(self) -> Dict[str, Any]:
        try:
            props_path = self._working_dir() / "server.properties"
            if props_path.exists():
                with open(props_path, "r", encoding="UTF-8") as f:
                    return dict(javaproperties.load(f))
        except Exception:
            pass
        return {}

    def _level_name(self) -> str:
        return self._server_properties().get("level-name", "world") or "world"

    def _load_json(self, path: Path, default: Any) -> Any:
        try:
            if path.exists():
                with open(path, "r", encoding="UTF-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, path: Path, data: Any) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="UTF-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except Exception as e:
            self.server.logger.error(f"写入文件失败 {path}: {e}")
            return False

    def _load_usercache(self) -> List[Dict[str, Any]]:
        data = self._load_json(self._working_dir() / "usercache.json", [])
        return data if isinstance(data, list) else []

    def _list_playerdata(self) -> Dict[str, str]:
        """返回 {uuid: 文件路径}，来自 world/playerdata/*.dat。"""
        result: Dict[str, str] = {}
        try:
            playerdata_dir = self._working_dir() / self._level_name() / "playerdata"
            if playerdata_dir.exists():
                for f in playerdata_dir.glob("*.dat"):
                    uuid = format_uuid(f.stem)
                    if uuid:
                        result[uuid] = str(f)
        except Exception:
            pass
        return result

    def _uuid_from_usercache(self, name: str) -> Optional[str]:
        for entry in self._load_usercache():
            if entry.get("name") == name:
                return format_uuid(entry.get("uuid") or "")
        return None

    def _name_for_uuid(self, uuid: str) -> Optional[str]:
        target = uuid.lower().replace("-", "")
        for entry in self._load_usercache():
            if format_uuid(entry.get("uuid") or "").lower().replace("-", "") == target:
                return entry.get("name")
        # ops / whitelist 中可能带 name
        for path, key in (
            (self._working_dir() / "ops.json", "uuid"),
            (self._working_dir() / "whitelist.json", "uuid"),
        ):
            data = self._load_json(path, [])
            if isinstance(data, list):
                for item in data:
                    if format_uuid(item.get("uuid") or "").lower().replace("-", "") == target:
                        return item.get("name")
        return None

    # ------------------------------------------------------------------ #
    # 会话 / IP 记录（PLAYER_JOINED / PLAYER_LEFT 事件驱动）
    # ------------------------------------------------------------------ #

    def _load_stats(self) -> Dict[str, Any]:
        with _STATS_LOCK:
            # Retain the old override hook used by integrations/tests; production uses SQLite.
            data = get_state("player_stats", "data", _DEFAULT_STATS)
            if Path(PLAYER_STATS_PATH) != Path("./config/guguwebui/guguwebui_static/player_stats.json"):
                data = self._load_json(PLAYER_STATS_PATH, _DEFAULT_STATS)
            if not isinstance(data, dict):
                data = _DEFAULT_STATS
            return copy.deepcopy(data)

    def _save_stats(self, stats: Dict[str, Any]) -> None:
        with _STATS_LOCK:
            self._prune_sessions(stats)
            if Path(PLAYER_STATS_PATH) != Path("./config/guguwebui/guguwebui_static/player_stats.json"):
                self._save_json(PLAYER_STATS_PATH, stats)
            else:
                set_state("player_stats", "data", stats)

    @staticmethod
    def _prune_sessions(stats: Dict[str, Any]) -> None:
        """裁剪超期会话，控制 player_stats.json 体积（开环会话按加入时间判定）。"""
        cutoff = time.time() - PLAYER_STATS_SESSION_RETENTION_DAYS * 86400
        sessions = stats.get("sessions")
        if isinstance(sessions, list) and sessions:
            kept = [s for s in sessions if (s.get("l") or s.get("j") or 0) >= cutoff]
            if len(kept) != len(sessions):
                stats["sessions"] = kept

    def on_player_joined(self, server, player: str, info=None) -> None:
        """记录上线时间与 IP（真实玩家有 IP，Carpet 假人没有）。"""
        try:
            now = time.time()
            _JOIN_TIMES[player] = now
            stats = self._load_stats()
            entry = stats.setdefault("players", {}).setdefault(player, {})
            if not entry.get("first_seen"):
                entry["first_seen"] = now
            entry["last_seen"] = now

            ip = None
            try:
                ip = server.get_player_ip(player)
            except Exception:
                pass
            if ip:
                entry.setdefault("ips", [])
                if ip not in entry["ips"]:
                    entry["ips"].append(ip)
                entry["last_ip"] = ip

            if not entry.get("uuid"):
                try:
                    info_obj = server.get_player_info(player)
                    if info_obj and getattr(info_obj, "uuid", None):
                        entry["uuid"] = format_uuid(info_obj.uuid)
                except Exception:
                    entry["uuid"] = self._uuid_from_usercache(player)

            # 会话日志：先关闭该玩家的旧开环会话（事件丢失时防止重复计数），再追加新会话
            sessions = stats.setdefault("sessions", [])
            for s in sessions:
                if s.get("p") == player and s.get("l") is None:
                    s["l"] = now
            sessions.append(
                {
                    "p": player,
                    "u": entry.get("uuid"),
                    "j": now,
                    "l": None,
                    "ip": ip,
                }
            )

            self._save_stats(stats)
        except Exception as e:
            self.server.logger.debug(f"记录玩家上线数据失败: {e}")

    def on_player_left(self, server, player: str) -> None:
        """玩家下线：关闭会话日志并累计本次会话时长。"""
        try:
            now = time.time()
            join_time = _JOIN_TIMES.pop(player, None)
            stats = self._load_stats()

            # 关闭该玩家的开环会话；_JOIN_TIMES 丢失（插件重载）时以会话记录补回加入时间
            sessions = stats.setdefault("sessions", [])
            closed = False
            for s in reversed(sessions):
                if s.get("p") == player and s.get("l") is None:
                    if join_time is None and isinstance(s.get("j"), (int, float)):
                        join_time = s["j"]
                    s["l"] = now
                    closed = True
                    break

            entry = stats.setdefault("players", {}).setdefault(player, {})
            if join_time is not None:
                duration = now - join_time
                if duration > 0:
                    entry["total_playtime"] = entry.get("total_playtime", 0) + duration
            entry["last_seen"] = now
            if closed or join_time is not None:
                self._save_stats(stats)
        except Exception as e:
            self.server.logger.debug(f"记录玩家下线数据失败: {e}")

    # ------------------------------------------------------------------ #
    # 在线玩家 / IP 查询
    # ------------------------------------------------------------------ #

    def _get_online_players(self) -> List[str]:
        try:
            if self.server.is_rcon_running():
                feedback = self.server.rcon_query("list")
                if isinstance(feedback, str) and ":" in feedback:
                    names_part = feedback.split(":", 1)[1].strip()
                    if names_part:
                        return [
                            n.strip()
                            for n in names_part.split(",")
                            if n.strip()
                        ]
            return list(self.server.get_player_list() or [])
        except Exception:
            return []

    def _get_player_ip(self, name: str) -> Optional[str]:
        """在线玩家的 IP（假人通常返回 None）。"""
        try:
            return self.server.get_player_ip(name)
        except Exception:
            return None

    def _merge_player_logger_ips(self, roster: Dict[str, Dict[str, Any]]) -> None:
        """补充 player_ip_logger 插件记录的历史 IP（如果已安装）。"""
        try:
            plugin = self.server.get_plugin_instance("player_ip_logger")
            if not plugin or not hasattr(plugin, "get_player_ips"):
                return
            for rec in roster.values():
                name = rec.get("name")
                if not name:
                    continue
                try:
                    extra = plugin.get_player_ips(name)
                    if isinstance(extra, list):
                        for ip in extra:
                            if isinstance(ip, str) and ip and ip not in rec["ips"]:
                                rec["ips"].append(ip)
                except Exception:
                    continue
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 名单数据
    # ------------------------------------------------------------------ #

    def _ops_names(self) -> set:
        data = self._load_json(self._working_dir() / "ops.json", [])
        names = set()
        if isinstance(data, list):
            for item in data:
                if item.get("name"):
                    names.add(item["name"])
        return names

    def _whitelist_names(self) -> set:
        data = self._load_json(self._working_dir() / "whitelist.json", [])
        names = set()
        if isinstance(data, list):
            for item in data:
                if item.get("name"):
                    names.add(item["name"])
        return names

    def _banned_names(self) -> Dict[str, Dict[str, Any]]:
        data = self._load_json(self._working_dir() / "banned-players.json", [])
        result: Dict[str, Dict[str, Any]] = {}
        if isinstance(data, list):
            for item in data:
                name = item.get("name") or item.get("uuid") or ""
                if name:
                    result[name] = item
        return result

    def _banned_ips(self) -> List[str]:
        data = self._load_json(self._working_dir() / "banned-ips.json", [])
        ips = []
        if isinstance(data, list):
            for item in data:
                if item.get("ip"):
                    ips.append(item["ip"])
        return ips

    def _is_whitelist_enabled(self) -> bool:
        value = str(self._server_properties().get("white-list", "false")).strip().lower()
        return value in ("true", "1", "yes", "on")

    # ------------------------------------------------------------------ #
    # 玩家列表
    # ------------------------------------------------------------------ #

    def _build_roster(self) -> Dict[str, Dict[str, Any]]:
        """构建全量名单（按名称或 uuid 索引），不包含坐标（懒加载）。"""
        roster: Dict[str, Dict[str, Any]] = {}

        # usercache.json：所有进过服的玩家
        usercache = self._load_usercache()
        for entry in usercache:
            name = entry.get("name")
            uuid = format_uuid(entry.get("uuid") or "")
            key = name or uuid
            if not key:
                continue
            rec = roster.setdefault(
                key,
                {"name": name, "uuid": uuid, "ips": [], "online": False},
            )
            if name:
                rec["name"] = name
            if uuid:
                rec["uuid"] = uuid
            if entry.get("expiresOn"):
                rec["usercache_expires"] = entry["expiresOn"]

        # playerdata：usercache 之外的历史玩家（UUID 为主键）
        playerdata = self._list_playerdata()
        known_uuids = {r.get("uuid") for r in roster.values()}
        for uuid, path in playerdata.items():
            if uuid in known_uuids:
                continue
            name = self._name_for_uuid(uuid)
            key = name or uuid
            rec = roster.setdefault(
                key,
                {"name": name, "uuid": uuid, "ips": [], "online": False},
            )
            if name:
                rec["name"] = name
            if uuid and not rec.get("uuid"):
                rec["uuid"] = uuid
            rec["playerdata_path"] = path

        # WebUI 自身会话记录
        stats = self._load_stats()
        for name, entry in stats.get("players", {}).items():
            rec = roster.setdefault(
                name, {"name": name, "uuid": None, "ips": [], "online": False}
            )
            rec["ips"] = list(
                dict.fromkeys(rec.get("ips", []) + list(entry.get("ips", [])))
            )
            if entry.get("uuid") and not rec.get("uuid"):
                rec["uuid"] = format_uuid(entry["uuid"])
            if entry.get("last_ip") and not rec.get("ip"):
                rec["last_ip"] = entry["last_ip"]
            if entry.get("total_playtime"):
                rec["total_playtime"] = entry["total_playtime"]
            if entry.get("last_seen"):
                rec["last_seen"] = entry["last_seen"]

        # 在线玩家（含在线 IP 实时查询，用于假人判定）
        online = self._get_online_players()
        for name in online:
            rec = roster.setdefault(
                name, {"name": name, "uuid": None, "ips": [], "online": True}
            )
            rec["online"] = True
            ip = self._get_player_ip(name)
            if ip:
                rec.setdefault("ips", [])
                if ip not in rec["ips"]:
                    rec["ips"].append(ip)
                rec["last_ip"] = ip
            if not rec.get("uuid"):
                try:
                    info_obj = self.server.get_player_info(name)
                    if info_obj and getattr(info_obj, "uuid", None):
                        rec["uuid"] = format_uuid(info_obj.uuid)
                except Exception:
                    pass

        # player_ip_logger 历史 IP 补充
        self._merge_player_logger_ips(roster)

        # 标记字段
        ops_names = self._ops_names()
        whitelist_names = self._whitelist_names()
        banned_players = self._banned_names()
        banned_ips = self._banned_ips()
        now = time.time()

        for rec in roster.values():
            name = rec.get("name")
            uuid = rec.get("uuid")
            ips = rec.get("ips", [])
            rec["online"] = bool(rec.get("online"))
            if not name and uuid:
                rec["name"] = uuid
                rec["uuid_only"] = True
            rec["is_op"] = bool(name and name in ops_names)
            rec["whitelisted"] = bool(name and name in whitelist_names)
            rec["banned"] = bool(
                (name and name in banned_players)
                or (uuid and uuid in banned_players)
                or any(ip in banned_ips for ip in ips)
            )
            rec["is_bot"] = len(ips) == 0
            rec["ip"] = rec.get("last_ip") or (ips[0] if ips else None)

            if not rec.get("last_seen"):
                last_seen = _parse_expires_on(rec.get("usercache_expires"))
                if last_seen:
                    rec["last_seen"] = last_seen
                elif rec.get("playerdata_path"):
                    try:
                        rec["last_seen"] = os.path.getmtime(rec["playerdata_path"])
                    except OSError:
                        pass

            if rec["online"]:
                join_time = _JOIN_TIMES.get(name)
                rec["session_seconds"] = (
                    max(0, now - join_time) if join_time is not None else None
                )
            else:
                rec["session_seconds"] = None

        return roster

    def _enrich_position(self, rec: Dict[str, Any]) -> None:
        """填充坐标 / 维度：在线用 RCON data get，离线读 playerdata NBT。"""
        name = rec.get("name")
        uuid = rec.get("uuid")

        if rec.get("online") and name:
            pos = self._rcon_entity_data(name, "Pos")
            dim = self._rcon_entity_data(name, "Dimension")
            if pos is not None:
                rec["position"] = pos
            if dim is not None:
                rec["dimension"] = dim
            if pos is not None or dim is not None:
                return

        if uuid:
            data = self._playerdata_cached(uuid)
            if data.get("pos"):
                rec["position"] = data["pos"]
            if data.get("dimension"):
                rec["dimension"] = data["dimension"]

    def _rcon_entity_data(self, name: str, path: str):
        """通过 RCON 查询实体 NBT 数据（带 30s 缓存）。"""
        cache_key = f"player_entity:{name}:{path}"
        cached = api_cache.get(cache_key, ttl=30)
        if cached is not None:
            return cached
        value = None
        try:
            if self.server.is_rcon_running():
                feedback = self.server.rcon_query(f"data get entity {name} {path}")
                if path == "Pos":
                    value = _parse_pos_feedback(feedback)
                else:
                    value = _parse_dimension_feedback(feedback)
        except Exception:
            value = None
        api_cache.set(cache_key, value, ttl=30)
        return value

    def _playerdata_cached(self, uuid: str) -> Dict[str, Any]:
        cache_key = f"player_playerdata:{uuid}"
        cached = api_cache.get(cache_key, ttl=60)
        if cached is not None:
            return cached
        data: Dict[str, Any] = {}
        path = self._working_dir() / self._level_name() / "playerdata" / f"{uuid}.dat"
        if path.exists():
            data = read_playerdata(path)
        api_cache.set(cache_key, data, ttl=60)
        return data

    def get_players(
        self,
        search: str = "",
        filter_: str = "all",
        offset: int = 0,
        limit: int = 50,
        exclude_bots: bool = False,
    ) -> Dict[str, Any]:
        """汇总玩家列表，支持搜索与筛选；exclude_bots 排除假人（仅列出真实玩家）。"""
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        search = (search or "").strip().lower()

        roster = self._build_roster()
        players = list(roster.values())

        if search:
            players = [
                p
                for p in players
                if search in (p.get("name") or "").lower()
                or search in (p.get("uuid") or "").lower()
                or any(search in ip for ip in p.get("ips", []))
            ]

        if filter_ == "online":
            players = [p for p in players if p.get("online")]
        elif filter_ == "offline":
            players = [p for p in players if not p.get("online")]
        elif filter_ == "bot":
            players = [p for p in players if p.get("is_bot")]
        elif filter_ == "op":
            players = [p for p in players if p.get("is_op")]

        if exclude_bots:
            players = [p for p in players if not p.get("is_bot")]

        # 排序：在线优先，其次按最后在线时间倒序
        players.sort(
            key=lambda p: (
                0 if p.get("online") else 1,
                -(p.get("last_seen") or 0),
                (p.get("name") or "").lower(),
            )
        )

        total = len(players)
        page = players[offset : offset + limit]
        for rec in page:
            self._enrich_position(rec)

        online_count = sum(1 for p in players if p.get("online"))
        bot_count = sum(1 for p in players if p.get("is_bot"))

        return {
            "players": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "online_count": online_count,
            "bot_count": bot_count,
            "server_running": self.server.is_server_running(),
        }

    def get_bots(self) -> Dict[str, Any]:
        """识别出的假人列表（无 IP 记录的玩家 + 在线假人）。"""
        roster = self._build_roster()
        bots = [rec for rec in roster.values() if rec.get("is_bot")]
        bots.sort(
            key=lambda p: (
                0 if p.get("online") else 1,
                -(p.get("last_seen") or 0),
                (p.get("name") or "").lower(),
            )
        )
        for rec in bots:
            self._enrich_position(rec)
        return {
            "bots": bots,
            "total": len(bots),
            "server_running": self.server.is_server_running(),
        }

    # ------------------------------------------------------------------ #
    # 在线情况统计（会话日志驱动）
    # ------------------------------------------------------------------ #

    def _range_seconds(self, range_key: str) -> int:
        """统计时间范围（秒），复用服务器状态页的 RANGE_MAP。"""
        return RANGE_MAP.get(range_key, RANGE_MAP["1h"])

    def _reconcile_open_sessions(self) -> None:
        """兜底：关闭已不在线玩家的开环会话（丢下线事件 / 插件重载 / 服务器崩溃）。"""
        try:
            online = set(self._get_online_players())
            stats = self._load_stats()
            sessions = stats.get("sessions")
            if not isinstance(sessions, list) or not sessions:
                return
            now = time.time()
            changed = False
            for s in sessions:
                if s.get("l") is None and s.get("p") not in online:
                    s["l"] = now
                    changed = True
            if changed:
                self._save_stats(stats)
        except Exception:
            pass

    def _active_sessions(
        self, start: float, end: float, exclude_bots: bool
    ) -> Tuple[List[Dict[str, Any]], set]:
        """窗口内活跃的会话与玩家集合（可排除无 IP 玩家/假人）。"""
        stats = self._load_stats()
        sessions = stats.get("sessions") or []
        if exclude_bots:
            sessions = [s for s in sessions if s.get("ip")]
        active = []
        players = set()
        for s in sessions:
            j = s.get("j") or 0
            l = s.get("l")
            if j > end or (l is not None and l < start):
                continue
            active.append(s)
            if s.get("p"):
                players.add(s["p"])
        return active, players

    @staticmethod
    def _online_curve(
        sessions: List[Dict[str, Any]], start: float, end: float
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """扫描线求窗口内并发曲线：返回 (每分钟序列 [{t, value}], 峰值, 峰值时刻)。

        join 事件 +1、leave 事件 -1，前缀和即为任意时刻的精确并发数，
        峰值与峰值时刻均为精确值（而非采样近似）。
        """
        baseline = 0
        events = []
        for s in sessions:
            j = s.get("j") or 0
            l = s.get("l")
            if j > end or (l is not None and l < start):
                continue
            if l is not None and l < j:
                l = j
            if j < start:
                baseline += 1
            else:
                events.append((j, 1))
            if l is not None and l <= end:
                events.append((l, -1))
        events.sort(key=lambda e: (e[0], e[1]))

        cnt = baseline
        peak = baseline
        peak_ts = int(start)
        points = []
        idx = 0
        n = len(events)
        first_minute = int(start // 60) * 60
        if first_minute < start:
            first_minute += 60
        m = first_minute
        while m <= end:
            while idx < n and events[idx][0] <= m:
                cnt += events[idx][1]
                if cnt > peak:
                    peak = cnt
                    peak_ts = int(events[idx][0])
                idx += 1
            points.append({"t": m, "value": cnt})
            m += 60
        # 处理最后一个不足一分钟窗口内的事件（同时用于峰值计算），
        # 并补一个窗口终点采样点，避免该分钟内的在线活动在曲线上“隐身”
        while idx < n and events[idx][0] <= end:
            cnt += events[idx][1]
            if cnt > peak:
                peak = cnt
                peak_ts = int(events[idx][0])
            idx += 1
        if points and points[-1]["t"] < int(end):
            points.append({"t": int(end), "value": cnt})
        return points, peak, peak_ts

    @staticmethod
    def _downsample_points(
        points: List[Dict[str, Any]], max_points: int = 1500
    ) -> List[Dict[str, Any]]:
        """相邻桶取均值降采样，控制返回点数。"""
        if len(points) <= max_points:
            return points
        bucket = len(points) / max_points
        result = []
        idx = 0.0
        while idx < len(points):
            start_i = int(idx)
            end_i = min(int(idx + bucket), len(points))
            chunk = points[start_i:end_i]
            if not chunk:
                break
            vals = [c["value"] for c in chunk if c.get("value") is not None]
            result.append(
                {
                    "t": chunk[0]["t"],
                    "value": round(sum(vals) / len(vals), 2) if vals else 0,
                }
            )
            idx += bucket
        return result

    def get_stats_overview(
        self, range_key: str = "1h", exclude_bots: bool = False
    ) -> Dict[str, Any]:
        """在线情况摘要：当前 / 平均 / 峰值在线、活跃玩家数、会话数。"""
        range_seconds = self._range_seconds(range_key)
        end = time.time()
        start = end - range_seconds
        self._reconcile_open_sessions()
        sessions, active_players = self._active_sessions(start, end, exclude_bots)

        # 当前在线（以实时名单为准，排除无 IP 玩家时按历史 IP 证据过滤）
        online_names = self._get_online_players()
        if exclude_bots:
            stats_all = self._load_stats()
            real_players = {
                n for n, e in stats_all.get("players", {}).items() if e.get("ips")
            }
            for s in sessions:
                if s.get("ip") and s.get("p"):
                    real_players.add(s["p"])
            current_online = sum(1 for n in online_names if n in real_players)
        else:
            current_online = len(online_names)

        # 平均在线：窗口内玩家分钟数 / 窗口时长
        player_minutes = 0.0
        for s in sessions:
            j = s.get("j") or 0
            l = s.get("l")
            eff_l = l if l is not None else end
            player_minutes += max(0.0, min(eff_l, end) - max(j, start))
        avg_online = round(player_minutes / range_seconds, 2) if range_seconds > 0 else 0.0

        _, peak, peak_ts = self._online_curve(sessions, start, end)

        return {
            "range": range_key,
            "current_online": current_online,
            "avg_online": avg_online,
            "peak_online": peak,
            "peak_ts": peak_ts,
            "active_players": len(active_players),
            "total_sessions": len(sessions),
        }

    def get_stats_online_history(
        self, range_key: str = "1h", exclude_bots: bool = False
    ) -> Dict[str, Any]:
        """在线人数曲线：按分钟分桶 + 降采样。"""
        range_seconds = self._range_seconds(range_key)
        end = time.time()
        start = end - range_seconds
        self._reconcile_open_sessions()
        sessions, _ = self._active_sessions(start, end, exclude_bots)
        points, _, _ = self._online_curve(sessions, start, end)
        return {
            "range": range_key,
            "sample": "1m",
            "points": self._downsample_points(points),
        }

    def get_stats_daily(
        self, range_key: str = "7d", exclude_bots: bool = False
    ) -> Dict[str, Any]:
        """每日活跃统计：唯一玩家 / 会话数 / 在线时长（本地时区按天分组）。"""
        range_seconds = self._range_seconds(range_key)
        end = time.time()
        start = end - range_seconds
        self._reconcile_open_sessions()
        sessions, _ = self._active_sessions(start, end, exclude_bots)

        day0 = datetime.datetime.fromtimestamp(start).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        now_dt = datetime.datetime.fromtimestamp(end)
        points = []
        cur = day0
        while cur <= now_dt:
            day_begin = cur.timestamp()
            day_end = (cur + datetime.timedelta(days=1)).timestamp()
            eff_begin = max(day_begin, start)
            eff_end = min(day_end, end)
            if eff_end > eff_begin:
                players = set()
                session_count = 0
                playtime = 0.0
                for s in sessions:
                    j = s.get("j") or 0
                    l = s.get("l")
                    if l is not None and l < eff_begin:
                        continue
                    if j > eff_end:
                        continue
                    if s.get("p"):
                        players.add(s["p"])
                    if eff_begin <= j < eff_end:
                        session_count += 1
                    eff_l = min(l, end) if l is not None else min(eff_end, end)
                    playtime += max(0.0, min(eff_l, eff_end) - max(j, eff_begin))
                points.append(
                    {
                        "date": cur.strftime("%Y-%m-%d"),
                        "players": len(players),
                        "sessions": session_count,
                        "playtime": round(playtime),
                    }
                )
            cur = cur + datetime.timedelta(days=1)
        return {"range": range_key, "points": points}

    def get_stats_players(
        self, exclude_bots: bool = False, limit: int = 50
    ) -> Dict[str, Any]:
        """玩家在线统计排行：累计时长（聚合，含历史）+ 会话派生指标。"""
        limit = max(1, min(int(limit or 50), 200))
        self._reconcile_open_sessions()
        stats = self._load_stats()
        players_data = stats.get("players", {})
        sessions = stats.get("sessions") or []

        # 真实玩家判定：聚合 IP 记录或任意会话带 IP
        real_players = {n for n, e in players_data.items() if e.get("ips")}
        for s in sessions:
            if s.get("ip") and s.get("p"):
                real_players.add(s["p"])

        online_now = set(self._get_online_players())
        now = time.time()

        per_player: Dict[str, Dict[str, Any]] = {}
        for s in sessions:
            p = s.get("p")
            if not p:
                continue
            rec = per_player.setdefault(p, {"sessions": 0, "playtime": 0.0})
            rec["sessions"] += 1
            j = s.get("j") or 0
            l = s.get("l")
            if l is not None:
                rec["playtime"] += max(0.0, l - j)
            else:
                rec["playtime"] += max(0.0, now - j)

        rows = []
        for name, rec in per_player.items():
            if exclude_bots and name not in real_players:
                continue
            data = players_data.get(name, {})
            agg_playtime = data.get("total_playtime")
            if not isinstance(agg_playtime, (int, float)):
                agg_playtime = rec["playtime"]
            rows.append(
                {
                    "name": name,
                    "uuid": data.get("uuid"),
                    "online": name in online_now,
                    "sessions": rec["sessions"],
                    "total_playtime": round(agg_playtime, 1),
                    "avg_session": (
                        round(rec["playtime"] / rec["sessions"], 1)
                        if rec["sessions"]
                        else 0
                    ),
                    "first_seen": data.get("first_seen"),
                    "last_seen": data.get("last_seen"),
                }
            )

        rows.sort(
            key=lambda r: (
                -(r["total_playtime"] or 0),
                -(r["sessions"] or 0),
                (r["name"] or "").lower(),
            )
        )
        return {"players": rows[:limit], "total": len(rows)}

    # ------------------------------------------------------------------ #
    # 命令执行
    # ------------------------------------------------------------------ #

    def _run_mc_command(self, command: str) -> Dict[str, Any]:
        """执行 MC 原生命令：优先 RCON（可获取反馈），否则走 MCDR 命令转发。"""
        try:
            if self.server.is_rcon_running():
                try:
                    feedback = self.server.rcon_query(command)
                    return {"ok": True, "feedback": feedback}
                except Exception as e:
                    self.server.logger.debug(
                        f"RCON 执行失败，回退到 MCDR 转发: {command} ({e})"
                    )
        except Exception:
            pass
        try:
            self.server.execute_command("/" + command)
            return {"ok": True, "feedback": None}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _server_running(self) -> bool:
        try:
            return self.server.is_server_running()
        except Exception:
            return False

    def _not_running(self) -> Dict[str, Any]:
        return {
            "status": "error",
            "code": "server_not_running",
            "message": "服务器未运行，无法执行该命令",
            "server_running": False,
        }

    # ------------------------------------------------------------------ #
    # 白名单
    # ------------------------------------------------------------------ #

    def get_whitelist(self) -> Dict[str, Any]:
        data = self._load_json(self._working_dir() / "whitelist.json", [])
        members = []
        usercache_uuid = {
            format_uuid(e.get("uuid") or ""): e.get("name")
            for e in self._load_usercache()
        }
        if isinstance(data, list):
            for item in data:
                uuid = format_uuid(item.get("uuid") or "")
                name = item.get("name") or usercache_uuid.get(uuid) or ""
                members.append({"name": name, "uuid": uuid})
        return {
            "status": "success",
            "enabled": self._is_whitelist_enabled(),
            "members": members,
            "server_running": self._server_running(),
        }

    def set_whitelist_enabled(self, enabled: bool) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command(f"whitelist {'on' if enabled else 'off'}")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        return {
            "status": "success",
            "message": "白名单已开启" if enabled else "白名单已关闭",
            "enabled": enabled,
        }

    def reload_whitelist(self) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command("whitelist reload")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        return {"status": "success", "message": "白名单已重载"}

    def whitelist_add(self, name: str) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command(f"whitelist add {name}")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        # 更新后自动触发白名单重载
        self._run_mc_command("whitelist reload")
        return {"status": "success", "message": f"已将 {name} 加入白名单"}

    def whitelist_remove(self, name: str) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command(f"whitelist remove {name}")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        self._run_mc_command("whitelist reload")
        return {"status": "success", "message": f"已将 {name} 移出白名单"}

    # ------------------------------------------------------------------ #
    # OP 管理
    # ------------------------------------------------------------------ #

    def get_ops(self) -> Dict[str, Any]:
        data = self._load_json(self._working_dir() / "ops.json", [])
        ops = []
        if isinstance(data, list):
            for item in data:
                ops.append(
                    {
                        "name": item.get("name") or "",
                        "uuid": format_uuid(item.get("uuid") or ""),
                        "level": item.get("level"),
                        "bypassesPlayerLimit": item.get("bypassesPlayerLimit", False),
                    }
                )
        return {
            "status": "success",
            "ops": ops,
            "server_running": self._server_running(),
        }

    def op_player(self, name: str) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command(f"op {name}")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        return {"status": "success", "message": f"已将 {name} 设为 OP"}

    def deop_player(self, name: str) -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        result = self._run_mc_command(f"deop {name}")
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        return {"status": "success", "message": f"已取消 {name} 的 OP"}

    # ------------------------------------------------------------------ #
    # 封禁
    # ------------------------------------------------------------------ #

    def get_bans(self) -> Dict[str, Any]:
        players_data = self._load_json(
            self._working_dir() / "banned-players.json", []
        )
        players = []
        if isinstance(players_data, list):
            for item in players_data:
                players.append(
                    {
                        "uuid": format_uuid(item.get("uuid") or ""),
                        "name": item.get("name") or "",
                        "reason": item.get("reason") or "",
                        "created": item.get("created") or "",
                        "expires": item.get("expires") or "",
                        "source": item.get("source") or "",
                    }
                )
        ips_data = self._load_json(self._working_dir() / "banned-ips.json", [])
        ips = []
        if isinstance(ips_data, list):
            for item in ips_data:
                ips.append(
                    {
                        "ip": item.get("ip") or "",
                        "reason": item.get("reason") or "",
                        "created": item.get("created") or "",
                        "expires": item.get("expires") or "",
                        "source": item.get("source") or "",
                    }
                )
        return {
            "status": "success",
            "players": players,
            "ips": ips,
            "server_running": self._server_running(),
        }

    def _vanilla_timestamp(self) -> str:
        return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    def ban_player(self, name: str, reason: str = "") -> Dict[str, Any]:
        if self._server_running():
            cmd = f"ban {name}"
            if reason:
                cmd += f" {reason}"
            result = self._run_mc_command(cmd)
            if not result.get("ok"):
                return {
                    "status": "error",
                    "code": "command_failed",
                    "message": result.get("error", "命令执行失败"),
                }
            return {
                "status": "success",
                "message": f"已封禁玩家 {name}",
                "needs_restart": False,
            }

        # 服务器离线：直接修改 banned-players.json（重启后生效）
        path = self._working_dir() / "banned-players.json"
        entries = self._load_json(path, [])
        if not isinstance(entries, list):
            entries = []
        entries = [e for e in entries if (e.get("name") or "") != name]
        entries.append(
            {
                "uuid": self._uuid_from_usercache(name) or "",
                "name": name,
                "created": self._vanilla_timestamp(),
                "source": "WebUI",
                "expires": "forever",
                "reason": reason or "由 WebUI 封禁",
            }
        )
        if not self._save_json(path, entries):
            return {"status": "error", "code": "file_write_failed", "message": "写入 banned-players.json 失败"}
        return {
            "status": "success",
            "message": f"已通过文件封禁玩家 {name}（重启服务器后生效）",
            "needs_restart": True,
        }

    def ban_ip(self, ip: str, reason: str = "") -> Dict[str, Any]:
        if self._server_running():
            cmd = f"ban-ip {ip}"
            if reason:
                cmd += f" {reason}"
            result = self._run_mc_command(cmd)
            if not result.get("ok"):
                return {
                    "status": "error",
                    "code": "command_failed",
                    "message": result.get("error", "命令执行失败"),
                }
            return {
                "status": "success",
                "message": f"已封禁 IP {ip}",
                "needs_restart": False,
            }

        path = self._working_dir() / "banned-ips.json"
        entries = self._load_json(path, [])
        if not isinstance(entries, list):
            entries = []
        entries = [e for e in entries if (e.get("ip") or "") != ip]
        entries.append(
            {
                "ip": ip,
                "created": self._vanilla_timestamp(),
                "source": "WebUI",
                "expires": "forever",
                "reason": reason or "由 WebUI 封禁",
            }
        )
        if not self._save_json(path, entries):
            return {"status": "error", "code": "file_write_failed", "message": "写入 banned-ips.json 失败"}
        return {
            "status": "success",
            "message": f"已通过文件封禁 IP {ip}（重启服务器后生效）",
            "needs_restart": True,
        }

    def unban_player(self, name: str) -> Dict[str, Any]:
        """解封玩家：仅通过修改 banned-players.json，重启服务器后生效。"""
        path = self._working_dir() / "banned-players.json"
        entries = self._load_json(path, [])
        if not isinstance(entries, list):
            entries = []
        target = name.strip().lower()
        remaining = []
        removed = False
        for e in entries:
            match_name = (e.get("name") or "").lower() == target
            match_uuid = (
                format_uuid(e.get("uuid") or "").lower()
                == format_uuid(name).lower()
            )
            if match_name or match_uuid:
                removed = True
                continue
            remaining.append(e)
        if not removed:
            return {"status": "error", "code": "ban_not_found", "message": f"未找到玩家 {name} 的封禁记录"}
        if not self._save_json(path, remaining):
            return {"status": "error", "code": "file_write_failed", "message": "写入 banned-players.json 失败"}
        return {
            "status": "success",
            "message": f"已解封玩家 {name}（重启服务器后生效）",
            "needs_restart": True,
        }

    def unban_ip(self, ip: str) -> Dict[str, Any]:
        """解封 IP：仅通过修改 banned-ips.json，重启服务器后生效。"""
        path = self._working_dir() / "banned-ips.json"
        entries = self._load_json(path, [])
        if not isinstance(entries, list):
            entries = []
        target = ip.strip()
        remaining = [e for e in entries if (e.get("ip") or "") != target]
        if len(remaining) == len(entries):
            return {"status": "error", "code": "ban_not_found", "message": f"未找到 IP {ip} 的封禁记录"}
        if not self._save_json(path, remaining):
            return {"status": "error", "code": "file_write_failed", "message": "写入 banned-ips.json 失败"}
        return {
            "status": "success",
            "message": f"已解封 IP {ip}（重启服务器后生效）",
            "needs_restart": True,
        }

    # ------------------------------------------------------------------ #
    # 踢出
    # ------------------------------------------------------------------ #

    def kick_player(self, name: str, reason: str = "") -> Dict[str, Any]:
        if not self._server_running():
            return self._not_running()
        cmd = f"kick {name}"
        if reason:
            cmd += f" {reason}"
        result = self._run_mc_command(cmd)
        if not result.get("ok"):
            return {"status": "error", "code": "command_failed", "message": result.get("error", "命令执行失败")}
        return {"status": "success", "message": f"已将 {name} 踢出服务器"}
