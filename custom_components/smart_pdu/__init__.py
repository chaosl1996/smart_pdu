"""Smart PDU integration.

设备为 TCP 客户端，本集成作为服务端监听端口（对应 Node-RED 中 tcp in 的
listen 模式）。协议（文本帧，以 START ... END 包裹）：

- 握手:  设备 -> `START login ...`，服务端必须回复 `Login Successful`
- 状态:  设备 -> `START iostate total='8' io8='<0-255>' check='..' END`
- 查询:  服务端 -> `START PVC_get check='136' END`
         设备   -> `START PVC_Info p='..' v='..' c='..' c0..c7='..' e='..' END`
- 控制:  服务端 -> `START open|close io='<位掩码>' check='..' END`
         设备回显同格式帧确认

check 校验 = "START" 与 "check='" 之间内容（去首尾空白）的 ASCII 字节和 & 0xFF。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BIT_ORDER_LSB,
    CMD_PVC_GET,
    CONF_BIT_ORDER,
    CONF_PORT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_BIT_ORDER,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LOGIN_REPLY,
    NUM_OUTLETS,
    POLL_TIMEOUT,
    STALE_AFTER,
)

PLATFORMS = ["switch", "sensor"]

_LOGGER = logging.getLogger(__name__)

RE_IO8 = re.compile(r"io8='(\d+)'")
RE_IO = re.compile(r"io='(\d+)'")
# 长键在前，避免 c 抢先匹配 c0..c7 的前缀
RE_PVC = re.compile(r"(p|v|f|e|c7|c6|c5|c4|c3|c2|c1|c0|c)='([\d.]+)'")

MAX_BUFFER = 8192


def build_command(middle: str) -> str:
    """按协议生成带 check 校验的完整指令帧。"""
    checksum = sum(ord(ch) for ch in middle) & 0xFF
    return f"START {middle} check='{checksum}' END"


class PduConnection:
    """一条来自 PDU 设备的 TCP 连接。"""

    def __init__(self, hub: PduHub, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.hub = hub
        self.reader = reader
        self.writer = writer
        peer = writer.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)
        self.last_rx = time.monotonic()
        self._buffer = b""

    async def run(self) -> None:
        """读取循环：按帧容错提取，不依赖换行分隔。"""
        _LOGGER.info("PDU connected from %s", self.peer)
        self.hub.connections.add(self)
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break
                self.last_rx = time.monotonic()
                self._feed(data)
        except (ConnectionResetError, OSError):
            pass
        finally:
            _LOGGER.info("PDU disconnected: %s", self.peer)
            self.hub.connections.discard(self)
            self.hub.on_disconnect()
            await self.close()

    def _feed(self, data: bytes) -> None:
        """缓冲并切分出 START ... END 帧，容忍任意分包/粘包。"""
        self._buffer += data
        while True:
            start = self._buffer.find(b"START")
            if start == -1:
                # 保留末尾最多 4 字节，防止 "START" 本身被拆在两次读之间
                self._buffer = self._buffer[-4:] if len(self._buffer) > 4 else self._buffer
                return
            end = self._buffer.find(b"END", start + 5)
            if end == -1:
                self._buffer = self._buffer[start:]
                if len(self._buffer) > MAX_BUFFER:  # 异常保护
                    self._buffer = b""
                return
            frame = self._buffer[start : end + 3].decode("utf-8", "replace")
            self._buffer = self._buffer[end + 3 :]
            self.hub.handle_frame(self, frame)

    async def send(self, text: str) -> None:
        try:
            self.writer.write(text.encode("ascii"))
            await asyncio.wait_for(self.writer.drain(), 3)
        except asyncio.TimeoutError:
            _LOGGER.debug("Send to %s timed out; dropping connection", self.peer)
            self.hub.connections.discard(self)
            self.writer.close()
        except (ConnectionResetError, OSError) as err:
            _LOGGER.debug("Send to %s failed: %s", self.peer, err)
            self.hub.connections.discard(self)
            self.writer.close()

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionResetError, OSError):
            pass


class PduHub:
    """TCP 服务端与协议状态机，一个配置项一个实例。"""

    def __init__(self, hass: HomeAssistant, port: int, bit_order: str) -> None:
        self.hass = hass
        self.port = port
        self.bit_order = bit_order
        self.outlets: dict[int, bool] = {i: False for i in range(1, NUM_OUTLETS + 1)}
        self.metrics: dict[str, float] = {}
        self.connections: set[PduConnection] = set()
        self.pvc_event = asyncio.Event()
        self._recent_masks: dict[int, float] = {}
        self.coordinator: PduCoordinator | None = None
        self._server: asyncio.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

    @property
    def has_clients(self) -> bool:
        return bool(self.connections)

    @property
    def client_peers(self) -> list[str]:
        """当前接入的设备地址（ip:port），用于诊断展示。"""
        return sorted(conn.peer for conn in self.connections)

    def bit_for(self, outlet: int) -> int:
        """插座序号 -> 位掩码。"""
        if self.bit_order == BIT_ORDER_LSB:
            return 1 << (outlet - 1)
        return 1 << (NUM_OUTLETS - outlet)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, "0.0.0.0", self.port)
        self._serve_task = self.hass.async_create_background_task(
            self._server.serve_forever(), "smart_pdu_server", eager_start=True
        )
        self._watchdog_task = self.hass.async_create_background_task(
            self._watchdog(), "smart_pdu_watchdog", eager_start=True
        )
        _LOGGER.info("PDU server listening on 0.0.0.0:%s (waiting for device)", self.port)

    async def stop(self) -> None:
        for conn in list(self.connections):
            self.connections.discard(conn)
            conn.writer.close()
        if self._server is not None:
            self._server.close()
        for task in (self._serve_task, self._watchdog_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        _LOGGER.info("PDU server stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await PduConnection(self, reader, writer).run()

    async def _watchdog(self) -> None:
        """踢掉长时间无数据的死连接，逼设备重连重新握手。"""
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for conn in list(self.connections):
                if now - conn.last_rx > STALE_AFTER:
                    _LOGGER.info("Dropping stale PDU connection %s", conn.peer)
                    self.connections.discard(conn)
                    conn.writer.close()

    # ---------------- 协议处理 ----------------

    def handle_frame(self, conn: PduConnection, frame: str) -> None:
        low = frame.lower()

        if "login" in low:
            _LOGGER.info("PDU login from %s: %s", conn.peer, frame)
            self.hass.async_create_task(conn.send(LOGIN_REPLY))

            async def _first_poll():
                # 设备刚建立连接时查询未必就绪，稍等再拉首帧数据
                await asyncio.sleep(2)
                if self.coordinator is not None and self.has_clients:
                    await self.coordinator.async_refresh()

            self.hass.async_create_background_task(_first_poll(), "smart_pdu_first_poll")
            return

        if "iostate" in low:
            match = RE_IO8.search(frame)
            if match:
                byte = int(match.group(1)) & 0xFF
                for outlet in range(1, NUM_OUTLETS + 1):
                    self.outlets[outlet] = bool(byte & self.bit_for(outlet))
                _LOGGER.debug("iostate byte=%s -> %s", byte, self.outlets)
                self._push()
            return

        if "pvc_info" in low:
            for key, raw in RE_PVC.findall(frame):
                value = float(raw)
                if key not in ("p", "e"):  # 与 Node-RED 流程一致：仅 v/c/f/c0..c7 除以 100
                    value = round(value / 100, 2)
                self.metrics[key] = value
            self.pvc_event.set()
            self._push()
            return

        if "open" in low or "close" in low:
            match = RE_IO.search(frame)
            if match:
                raw = int(match.group(1))
                on = "open" in low
                now = time.monotonic()
                # 清理过期的已下发掩码记录
                self._recent_masks = {m: t for m, t in self._recent_masks.items() if now - t < 20}
                if raw in self._recent_masks:
                    # 设备回显我们刚下发的命令帧：io 为位掩码
                    mask = raw
                elif 1 <= raw <= NUM_OUTLETS:
                    # 设备自身界面操作推送：io 为插座序号（实测协议行为）
                    self.outlets[raw] = on
                    _LOGGER.debug("device push %s outlet=%s", "open" if on else "close", raw)
                    self._push()
                    return
                else:
                    mask = raw  # 其余情况按位掩码兜底
                for outlet in range(1, NUM_OUTLETS + 1):
                    if mask & self.bit_for(outlet):
                        self.outlets[outlet] = on
                _LOGGER.debug("control echo %s mask=%s -> %s", "open" if on else "close", mask, self.outlets)
                self._push()

    async def async_set_outlet(self, outlet: int, on: bool) -> None:
        if not self.has_clients:
            raise ConnectionError("PDU 未连接")
        action = "open" if on else "close"
        mask = self.bit_for(outlet)
        # 记录最近下发的掩码，用于把设备回显帧与设备主动推送帧区分开
        self._recent_masks[mask] = time.monotonic()
        await self.send_command(f"{action} io='{mask}'")
        # 乐观更新，等待设备回显/ iostate 修正
        self.outlets[outlet] = on
        self._push()

    async def send_command(self, middle: str) -> None:
        frame = build_command(middle)
        targets = list(self.connections)
        if not targets:
            raise ConnectionError("PDU 未连接")
        for conn in targets:
            await conn.send(frame)
        _LOGGER.debug("Sent: %s", frame)

    def on_disconnect(self) -> None:
        if self.coordinator is not None:
            self.coordinator.async_set_updated_data(self.snapshot())

    def snapshot(self) -> dict:
        return {"outlets": dict(self.outlets), "metrics": dict(self.metrics)}

    def _push(self) -> None:
        if self.coordinator is not None:
            self.coordinator.async_set_updated_data(self.snapshot())


class PduCoordinator(DataUpdateCoordinator[dict]):
    """周期性发送 PVC_get 拉取电参量；设备推送帧由 hub 实时写入。"""

    def __init__(self, hass: HomeAssistant, hub: PduHub, update_interval: int) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=update_interval))
        self.hub = hub
        hub.coordinator = self
        self.data = hub.snapshot()
        self._poll_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict:
        # 登录触发的立即刷新与周期轮询可能并发，加锁串行化并加硬超时，
        # 防止单次卡死把整个轮询协程拖住
        async with self._poll_lock:
            if not self.hub.has_clients:
                raise UpdateFailed("PDU 未连接（等待设备接入）")
            self.hub.pvc_event.clear()
            try:
                await asyncio.wait_for(self.hub.send_command(CMD_PVC_GET), 5)
            except asyncio.TimeoutError as err:
                raise UpdateFailed("发送 PVC_get 超时") from err
            except (ConnectionError, OSError) as err:
                raise UpdateFailed(f"发送 PVC_get 失败: {err}") from err
            try:
                await asyncio.wait_for(self.hub.pvc_event.wait(), POLL_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise UpdateFailed("PDU 未响应 PVC_get") from err
            return self.hub.snapshot()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    settings = {**entry.data, **entry.options}
    port = settings.get(CONF_PORT, DEFAULT_PORT)
    interval = settings.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    bit_order = settings.get(CONF_BIT_ORDER, DEFAULT_BIT_ORDER)

    hub = PduHub(hass, port, bit_order)
    coordinator = PduCoordinator(hass, hub, interval)
    try:
        await hub.start()
    except OSError as err:
        raise ConfigEntryNotReady(f"无法监听端口 {port}: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = hub
    # 不阻塞启动：设备未接入时实体显示不可用，接入后自动恢复
    hass.async_create_background_task(coordinator.async_refresh(), "smart_pdu_initial_poll")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_on_update))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: PduHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.stop()
    return unload_ok


async def async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
