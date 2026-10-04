import datetime
import json
import logging
import struct
from pathlib import Path

from .storage import connect, has_meta, set_meta
from guguwebui.constant import STATIC_PATH

logger = logging.getLogger(__name__)


class ChatLogger:
    """聊天消息记录器，将消息保存到二进制文件中"""

    def __init__(self, data_dir=None):
        if data_dir is None:
            data_dir = Path(STATIC_PATH)
        self.data_dir = Path(data_dir)
        self.chat_messages_file = self.data_dir / "chat_messages.bin"  # legacy import source
        self.message_positions_file = self.data_dir / "message_positions.json"  # legacy import source

        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._migrate_legacy_messages()

        # 内存缓存：最近的消息（最多缓存1000条）
        self._message_cache = []
        self._cache_max_size = 1000
        self._cache_loaded = False

        # 消息位置索引缓存
        self._positions_cache = {}
        self._positions_loaded = False

    def _migrate_legacy_messages(self):
        """Import the legacy binary log into SQLite once, preserving message IDs."""
        if has_meta("chat_messages_v1"):
            return
        conn = connect()
        try:
            if self.chat_messages_file.exists():
                try:
                    data = self.chat_messages_file.read_bytes()
                    offset = 0
                    while offset < len(data):
                        message, new_offset = self._unpack_message(data, offset)
                        if message is None or new_offset <= offset:
                            break
                        converted = self._convert_to_serializable(message)
                        conn.execute(
                            "INSERT OR IGNORE INTO chat_messages "
                            "(id,timestamp_ms,player_id,message,message_type,rtext_json,player_uuid) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (
                                converted["id"], converted["timestamp_ms"], converted["player_id"],
                                converted["message"], 2 if converted.get("is_plugin") else (
                                    1 if converted.get("message_source") == "webui" else 0
                                ), json.dumps(converted.get("rtext_data"), ensure_ascii=False)
                                if converted.get("rtext_data") is not None else None,
                                converted.get("uuid"),
                            ),
                        )
                        offset = new_offset
                except OSError:
                    pass
            conn.commit()
        finally:
            conn.close()
        set_meta("chat_messages_v1")

    def _init_index(self):
        return None

    def _read_index(self):
        return {"message_count": self.get_message_count(), "next_message_id": self.get_last_message_id() + 1}

    def _write_index(self, index):
        return None

    def _load_positions_index(self):
        """加载消息位置索引"""
        if self._positions_loaded:
            return self._positions_cache

        try:
            if self.message_positions_file.exists():
                with open(self.message_positions_file, 'r', encoding='utf-8') as f:
                    self._positions_cache = json.load(f)
            else:
                self._positions_cache = {}
        except (FileNotFoundError, json.JSONDecodeError):
            self._positions_cache = {}

        self._positions_loaded = True
        return self._positions_cache

    def _save_positions_index(self):
        """保存消息位置索引"""
        try:
            with open(self.message_positions_file, 'w', encoding='utf-8') as f:
                json.dump(self._positions_cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存位置索引失败: {e}")

    def _add_position_to_index(self, message_id, file_position):
        """添加消息位置到索引"""
        self._load_positions_index()
        self._positions_cache[str(message_id)] = file_position

        # 限制索引大小，只保留最近的10000条消息位置
        if len(self._positions_cache) > 10000:
            # 按消息ID排序，删除最老的消息位置
            sorted_ids = sorted(self._positions_cache.keys(), key=int)
            for old_id in sorted_ids[:-10000]:
                del self._positions_cache[old_id]

        self._save_positions_index()

    def _add_to_cache(self, message):
        """添加消息到内存缓存"""
        self._message_cache.append(message)
        # 保持缓存大小
        if len(self._message_cache) > self._cache_max_size:
            self._message_cache = self._message_cache[-self._cache_max_size:]

    def _load_cache_from_file(self):
        """从文件加载最近的消息到缓存"""
        if self._cache_loaded:
            return

        try:
            # 获取最近的消息填充缓存
            self._message_cache = self._get_recent_messages_from_file(self._cache_max_size)
            self._cache_loaded = True
        except Exception as e:
            logger.warning(f"加载缓存失败: {e}")
            self._message_cache = []
            self._cache_loaded = True

    def _get_recent_messages_from_file(self, limit):
        """从文件末尾获取最近的消息"""
        if not self.chat_messages_file.exists():
            return []

        messages = []
        try:
            # 采用反向读取策略
            file_size = self.chat_messages_file.stat().st_size
            chunk_size = min(file_size, 1024 * 1024)  # 最多读取1MB

            with open(self.chat_messages_file, 'rb') as f:
                if file_size <= chunk_size:
                    # 小文件直接读取全部
                    data = f.read()
                    messages = self._parse_all_messages_from_data(data)
                else:
                    # 大文件从末尾开始读取
                    f.seek(file_size - chunk_size)
                    data = f.read()
                    messages = self._parse_all_messages_from_data(data)

            # 按时间排序并取最近的
            messages.sort(key=lambda x: x['timestamp'], reverse=True)
            return messages[:limit]

        except Exception as e:
            logger.warning(f"读取最近消息失败: {e}")
            return []

    def _parse_all_messages_from_data(self, data):
        """从二进制数据解析所有消息"""
        messages = []
        offset = 0

        while offset < len(data):
            message, new_offset = self._unpack_message(data, offset)
            if message is None:
                break

            # 转换为可序列化格式（timestamp_str 为服务端预拼本地化字符串，已移除，前端用 epoch 秒集中格式化）
            serializable_message = {
                'id': message['id'],
                'player_id': message['player_id'],
                'message': message['message'],
                'timestamp': int(message['timestamp'].timestamp()),
                'timestamp_ms': int(message['timestamp'].timestamp() * 1000),
                'is_rtext': message.get('is_rtext', False),
                'rtext_data': message.get('rtext_data', None),
                'is_plugin': message.get('is_plugin', False),
                'plugin_id': message.get('plugin_id', None),
                'uuid': message.get('uuid', None),
                'message_source': message.get('message_source', 'game')
            }
            messages.append(serializable_message)
            offset = new_offset

        return messages

    def _get_next_message_id(self):
        """获取下一个消息ID"""
        index = self._read_index()
        return index.get("next_message_id", 1)

    @staticmethod
    def _pack_message(message_id, player_id, message, timestamp, rtext_data=None, message_type=0, player_uuid=None):
        """打包消息数据为二进制格式

        v3格式(JSON): [版本(1字节)=3][JSON长度(4字节)][JSON UTF-8]
        v2格式: [版本(1字节)][消息ID(8字节)][时间戳(8字节)][消息类型(1字节)][玩家ID长度(4字节)][玩家ID][消息长度(4字节)][消息][RText数据长度(4字节)][RText数据][UUID长度(4字节)][UUID]
        v1格式: [消息ID(8字节)][时间戳(8字节)][消息类型(1字节)][玩家ID长度(4字节)][玩家ID][消息长度(4字节)][消息][RText数据长度(4字节)][RText数据]

        消息类型: 0=玩家消息, 1=WebUI消息, 2=插件消息
        版本: 1=旧格式(兼容), 2=新格式(包含UUID), 3=JSON格式(便于使用和扩展)
        """
        if not isinstance(player_id, str) or not isinstance(message, str):
            raise ValueError("player_id 和 message 必须是字符串")

        if not player_id.strip() or not message.strip():
            raise ValueError("player_id 和 message 不能为空")

        # 使用 v3 格式（JSON，便于使用和扩展）
        version = 3
        timestamp_ms = int(timestamp.timestamp() * 1000)
        payload = {
            "id": message_id,
            "player_id": player_id,
            "message": message,
            "timestamp_ms": timestamp_ms,
            "message_type": message_type,
        }
        if rtext_data is not None:
            payload["rtext_data"] = rtext_data
        if player_uuid is not None:
            payload["uuid"] = str(player_uuid)
        json_str = json.dumps(payload, ensure_ascii=False)
        json_bytes = json_str.encode('utf-8')
        return (struct.pack('B', version) +
                struct.pack('I', len(json_bytes)) +
                json_bytes)

    @staticmethod
    def _unpack_message(data, offset):
        """从二进制数据中解包消息（支持 v1/v2/v3 格式）

        返回: (消息字典, 新的偏移量)
        """
        try:
            original_offset = offset
            if offset >= len(data):
                return None, original_offset

            first_byte = data[offset]

            # v3 格式：版本号=3，随后 [JSON长度(4字节)][JSON]
            if first_byte == 3:
                offset += 1
                if offset + 4 > len(data):
                    return None, original_offset
                json_len = struct.unpack('I', data[offset:offset + 4])[0]
                offset += 4
                if offset + json_len > len(data):
                    return None, original_offset
                try:
                    payload = json.loads(data[offset:offset + json_len].decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None, original_offset
                offset += json_len
                # 规范化为与 v1/v2 相同的 result 结构
                timestamp_ms = payload.get("timestamp_ms", 0)
                timestamp = datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.timezone.utc)
                message_type = payload.get("message_type", 0)
                player_uuid = payload.get("uuid")
                rtext_data = payload.get("rtext_data")
                result = {
                    'id': payload['id'],
                    'player_id': payload['player_id'],
                    'message': payload['message'],
                    'timestamp': timestamp,
                    'timestamp_str': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                }
                if message_type == 2:
                    result['is_plugin'] = True
                    result['plugin_id'] = payload['player_id']
                    result['uuid'] = None
                    result['message_source'] = 'plugin'
                elif message_type == 1:
                    result['is_plugin'] = False
                    result['plugin_id'] = None
                    result['uuid'] = player_uuid
                    result['message_source'] = 'webui'
                else:
                    result['is_plugin'] = False
                    result['plugin_id'] = None
                    result['uuid'] = player_uuid
                    result['message_source'] = 'game'
                result['is_rtext'] = rtext_data is not None
                result['rtext_data'] = rtext_data
                return result, offset

            # 尝试检测是否为 v1/v2 格式
            # v2 第一个字节是版本号 1 或 2，v1 无版本号
            is_new_format = False
            player_uuid = None
            if first_byte in [1, 2]:
                if offset + 9 <= len(data):
                    potential_msg_id = struct.unpack('Q', data[offset + 1:offset + 9])[0]
                    if 0 < potential_msg_id < 10 ** 10:
                        is_new_format = True
                        offset += 1

            # 读取消息ID (8字节)
            if offset + 8 > len(data):
                return None, original_offset

            message_id = struct.unpack('Q', data[offset:offset + 8])[0]
            offset += 8

            # 读取时间戳 (8字节)
            if offset + 8 > len(data):
                return None, original_offset

            timestamp_ms = struct.unpack('Q', data[offset:offset + 8])[0]
            offset += 8

            # 读取消息类型 (1字节)
            if offset + 1 > len(data):
                # 旧格式消息，默认为玩家消息
                message_type = 0
            else:
                message_type = struct.unpack('B', data[offset:offset + 1])[0]
                offset += 1

            # 读取玩家ID长度 (4字节)
            if offset + 4 > len(data):
                return None, original_offset

            player_id_len = struct.unpack('I', data[offset:offset + 4])[0]
            offset += 4

            # 读取玩家ID
            if offset + player_id_len > len(data):
                return None, original_offset

            player_id = data[offset:offset + player_id_len].decode('utf-8')
            offset += player_id_len

            # 读取消息长度 (4字节)
            if offset + 4 > len(data):
                return None, original_offset

            message_len = struct.unpack('I', data[offset:offset + 4])[0]
            offset += 4

            # 读取消息内容
            if offset + message_len > len(data):
                return None, original_offset

            message = data[offset:offset + message_len].decode('utf-8')
            offset += message_len

            # 读取RText数据长度 (4字节)
            if offset + 4 > len(data):
                # 旧格式消息，没有RText数据
                rtext_data = None
            else:
                rtext_len = struct.unpack('I', data[offset:offset + 4])[0]
                offset += 4

                # 读取RText数据
                if offset + rtext_len > len(data):
                    rtext_data = None
                else:
                    if rtext_len > 0:
                        try:
                            rtext_json = data[offset:offset + rtext_len].decode('utf-8')
                            rtext_data = json.loads(rtext_json)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            rtext_data = None
                    else:
                        rtext_data = None
                    offset += rtext_len

            # 读取UUID数据（仅新格式有）
            if is_new_format and offset + 4 <= len(data):
                uuid_len = struct.unpack('I', data[offset:offset + 4])[0]
                offset += 4

                # 读取UUID
                if offset + uuid_len <= len(data):
                    if uuid_len > 0:
                        try:
                            player_uuid = data[offset:offset + uuid_len].decode('utf-8')
                        except UnicodeDecodeError:
                            player_uuid = None
                    offset += uuid_len

            # 转换时间戳
            timestamp = datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.timezone.utc)

            result = {
                'id': message_id,
                'player_id': player_id,
                'message': message,
                'timestamp': timestamp,
                'timestamp_str': timestamp.strftime('%Y-%m-%d %H:%M:%S')
            }

            # 根据消息类型设置相关字段
            if message_type == 2:  # 插件消息
                result['is_plugin'] = True
                result['plugin_id'] = player_id
                result['uuid'] = None  # 插件消息没有UUID
                result['message_source'] = 'plugin'
            elif message_type == 1:  # WebUI消息
                result['is_plugin'] = False
                result['plugin_id'] = None
                result['uuid'] = player_uuid  # WebUI消息也可能有UUID
                result['message_source'] = 'webui'
            else:  # 玩家消息 (message_type == 0)
                result['is_plugin'] = False
                result['plugin_id'] = None
                result['uuid'] = player_uuid  # 从二进制文件中读取的UUID
                result['message_source'] = 'game'

            # 如果有RText数据，添加到结果中
            if rtext_data is not None:
                result['is_rtext'] = True
                result['rtext_data'] = rtext_data
            else:
                result['is_rtext'] = False
                result['rtext_data'] = None

            return result, offset

        except (struct.error, UnicodeDecodeError, ValueError) as e:
            logger.warning(f"解析消息失败: {e}")
            return None, original_offset

    def add_message(self, player_id, message, timestamp=None, rtext_data=None, message_type=0, player_uuid=None,
                    server=None):
        """添加新消息

        Args:
            player_id: 玩家ID
            message: 消息内容
            timestamp: 时间戳
            rtext_data: RText数据
            message_type: 消息类型 (0=玩家消息, 1=WebUI消息, 2=插件消息)
            player_uuid: 玩家UUID（如果提供则直接使用，否则自动获取）
            server: MCDR服务器接口（用于获取UUID）
        """
        if not isinstance(player_id, str) or not isinstance(message, str):
            raise ValueError("player_id 和 message 必须是字符串")

        if not player_id.strip() or not message.strip():
            raise ValueError("player_id 和 message 不能为空")

        if timestamp is None:
            timestamp = datetime.datetime.now(datetime.timezone.utc)

        # 获取UUID（仅对玩家消息和WebUI消息）
        if player_uuid is None and message_type in [0, 1] and server is not None:
            try:
                from .mc_util import get_player_uuid
                import asyncio

                # Run async function in sync context
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                if loop.is_running():
                    # This is tricky in a running event loop, but ChatLogger.add_message
                    # is usually called from MCDR thread which is sync.
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        player_uuid = pool.submit(lambda: asyncio.run(get_player_uuid(player_id, server))).result(
                            timeout=2.0)
                else:
                    player_uuid = loop.run_until_complete(get_player_uuid(player_id, server))
            except Exception:
                player_uuid = None  # 获取失败时设为None

        conn = connect()
        try:
            cursor = conn.execute(
                "INSERT INTO chat_messages "
                "(timestamp_ms,player_id,message,message_type,rtext_json,player_uuid) VALUES(?,?,?,?,?,?)",
                (
                    int(timestamp.timestamp() * 1000), player_id, message, int(message_type),
                    json.dumps(rtext_data, ensure_ascii=False) if rtext_data is not None else None,
                    player_uuid,
                ),
            )
            message_id = int(cursor.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self._add_to_cache(self._message_from_row({
            "id": message_id, "timestamp_ms": int(timestamp.timestamp() * 1000),
            "player_id": player_id, "message": message, "message_type": message_type,
            "rtext_json": json.dumps(rtext_data, ensure_ascii=False) if rtext_data is not None else None,
            "player_uuid": player_uuid,
        }))
        return message_id

    @staticmethod
    def _message_from_row(row):
        message_type = int(row["message_type"] or 0)
        try:
            rtext_data = json.loads(row["rtext_json"]) if row["rtext_json"] else None
        except (TypeError, json.JSONDecodeError):
            rtext_data = None
        timestamp_ms = int(row["timestamp_ms"] or 0)
        return {
            "id": int(row["id"]), "player_id": row["player_id"], "message": row["message"],
            "timestamp": timestamp_ms // 1000, "timestamp_ms": timestamp_ms,
            "is_rtext": rtext_data is not None, "rtext_data": rtext_data,
            "is_plugin": message_type == 2,
            "plugin_id": row["player_id"] if message_type == 2 else None,
            "uuid": row["player_uuid"],
            "message_source": "plugin" if message_type == 2 else ("webui" if message_type == 1 else "game"),
        }

    def _query_sql_messages(self, limit, offset=0, after_id=None, before_id=None):
        clauses = []
        params = []
        if after_id is not None:
            clauses.append("id > ?")
            params.append(int(after_id))
        if before_id is not None:
            clauses.append("id < ?")
            params.append(int(before_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = "ASC" if after_id is not None else "DESC"
        sql = "SELECT id,timestamp_ms,player_id,message,message_type,rtext_json,player_uuid " \
              f"FROM chat_messages{where} ORDER BY id {order} LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        conn = connect()
        try:
            return [self._message_from_row(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def get_messages(self, limit=50, offset=0, after_id=None, before_id=None):
        """获取消息；接口统一保证最新优先，游标查询按消息 ID 稳定排序。

        Args:
            limit: 限制返回的消息数量
            offset: 偏移量（用于向后兼容）
            after_id: 只返回ID大于此值的消息（新消息）
            before_id: 只返回ID小于此值的消息（历史消息）
        """
        try:
            return self._query_sql_messages(limit, offset=offset, after_id=after_id, before_id=before_id)
        except Exception as e:
            logger.warning(f"读取消息失败: {e}")
            return []

    def _get_new_messages_optimized(self, after_id, limit):
        """优化的新消息获取"""
        # 首先检查缓存
        self._load_cache_from_file()

        # 从缓存中筛选新消息
        new_messages = [msg for msg in self._message_cache if msg['id'] > after_id]

        if len(new_messages) >= limit:
            # 缓存中有足够的新消息
            new_messages.sort(key=lambda x: x['id'])
            return new_messages[:limit]

        # 缓存不足，需要从文件读取
        return self._get_messages_from_file_after_id(after_id, limit)

    def _get_recent_messages_optimized(self, limit):
        """优化的最近消息获取"""
        # 首先尝试从缓存获取
        self._load_cache_from_file()

        if len(self._message_cache) >= limit:
            # 缓存中有足够的消息
            sorted_messages = sorted(self._message_cache, key=lambda x: x['timestamp'], reverse=True)
            return sorted_messages[:limit]

        # 缓存不足，从文件读取
        return self._get_recent_messages_from_file(limit)

    def _get_historical_messages_optimized(self, before_id, limit):
        """优化的历史消息获取"""
        # 首先检查缓存
        self._load_cache_from_file()

        # 从缓存中筛选历史消息
        historical_messages = [msg for msg in self._message_cache if msg['id'] < before_id]

        if len(historical_messages) >= limit:
            # 缓存中有足够的历史消息
            historical_messages.sort(key=lambda x: x['timestamp'], reverse=True)
            return historical_messages[:limit]

        # 缓存不足，使用位置索引优化文件读取
        return self._get_historical_messages_from_file(before_id, limit)

    def _get_messages_from_file_after_id(self, after_id, limit):
        """从文件获取指定ID之后的消息"""
        positions = self._load_positions_index()
        messages = []

        try:
            with open(self.chat_messages_file, 'rb') as f:
                # 尝试使用位置索引快速定位
                start_position = 0
                if str(after_id) in positions:
                    start_position = positions[str(after_id)]

                # 找到一个合适的起始位置
                for msg_id in sorted([int(k) for k in positions.keys()]):
                    if msg_id > after_id:
                        start_position = positions[str(msg_id)]
                        break

                f.seek(start_position)
                data = f.read()

                offset = 0
                while offset < len(data) and len(messages) < limit:
                    message, new_offset = self._unpack_message(data, offset)
                    if message is None:
                        break

                    if message['id'] > after_id:
                        serializable_message = self._convert_to_serializable(message)
                        messages.append(serializable_message)

                    offset = new_offset

        except Exception as e:
            logger.warning(f"从文件读取新消息失败: {e}")

        return messages

    def _get_historical_messages_from_file(self, before_id, limit):
        """从文件获取历史消息"""
        # 为了效率，这里还是采用从末尾读取然后筛选的方式
        try:
            file_size = self.chat_messages_file.stat().st_size
            chunk_size = min(file_size, 2 * 1024 * 1024)  # 读取最多2MB

            with open(self.chat_messages_file, 'rb') as f:
                if file_size <= chunk_size:
                    data = f.read()
                else:
                    f.seek(file_size - chunk_size)
                    data = f.read()

                all_messages = self._parse_all_messages_from_data(data)
                historical_messages = [msg for msg in all_messages if msg['id'] < before_id]
                historical_messages.sort(key=lambda x: x['timestamp'], reverse=True)
                return historical_messages[:limit]

        except Exception as e:
            logger.warning(f"从文件读取历史消息失败: {e}")
            return []

    def _get_messages_with_offset(self, limit, offset):
        """传统的offset方式读取消息（兼容性）"""
        try:
            with open(self.chat_messages_file, 'rb') as f:
                data = f.read()

            messages = []
            offset_pos = 0
            messages_read = 0
            max_attempts = offset + limit + 100  # 防止无限循环
            attempt_count = 0

            while offset_pos < len(data) and messages_read < (offset + limit) and attempt_count < max_attempts:
                message, offset_pos = self._unpack_message(data, offset_pos)
                if message is None:
                    break

                messages_read += 1
                if messages_read > offset:
                    serializable_message = self._convert_to_serializable(message)
                    messages.append(serializable_message)

                attempt_count += 1

            messages.sort(key=lambda x: x['timestamp'], reverse=True)
            return messages

        except Exception as e:
            logger.warning(f"使用offset读取消息失败: {e}")
            return []

    @staticmethod
    def _convert_to_serializable(message):
        """将消息转换为可序列化格式"""
        return {
            'id': message['id'],
            'player_id': message['player_id'],
            'message': message['message'],
            'timestamp': int(message['timestamp'].timestamp()),
            'timestamp_ms': int(message['timestamp'].timestamp() * 1000),
            'is_rtext': message.get('is_rtext', False),
            'rtext_data': message.get('rtext_data', None),
            'is_plugin': message.get('is_plugin', False),
            'plugin_id': message.get('plugin_id', None),
            'uuid': message.get('uuid', None),
            'message_source': message.get('message_source', 'game')
        }

    def get_new_messages(self, after_id):
        """获取指定ID之后的新消息"""
        return self.get_messages(after_id=after_id, limit=100)

    def get_message_count(self):
        """获取消息总数"""
        conn = connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0])
        finally:
            conn.close()

    def get_last_message_id(self):
        """获取最后一条消息的ID"""
        conn = connect()
        try:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM chat_messages").fetchone()
            return int(row[0])
        finally:
            conn.close()

    def clear_messages(self):
        """清空所有消息"""
        conn = connect()
        try:
            conn.execute("DELETE FROM chat_messages")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='chat_messages'")
            conn.commit()
        finally:
            conn.close()
        if self.chat_messages_file.exists():
            self.chat_messages_file.unlink()
        if self.message_positions_file.exists():
            self.message_positions_file.unlink()

        # 清理内存缓存
        self._message_cache = []
        self._cache_loaded = False
        self._positions_cache = {}
        self._positions_loaded = False

    def get_file_size(self):
        """获取消息文件大小"""
        if self.chat_messages_file.exists():
            return self.chat_messages_file.stat().st_size
        return 0
