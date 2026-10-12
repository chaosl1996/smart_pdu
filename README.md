# Smart PDU

![Smart PDU](custom_components/smart_pdu/brand/icon.png)

**Home Assistant 智能PDU集成** —— 通过设备原生 TCP 协议直连 8 路智能排插/机柜电源，无需云端、无需刷机。

Native TCP integration for 8-outlet smart PDUs in Home Assistant — no cloud, no flashing.

## 功能特性

- **8 路独立插座控制**：开关状态双向同步，支持 optimistic 快速响应与设备回显双重确认
- **整机电参数监测**：总功率 (W)、电压 (V)、总电流 (A)、累计电能 (kWh，.total_increasing)
- **分插座电流监测**：8 路独立电流传感器
- **服务端模式**：HA 监听 TCP 端口，PDU 作为客户端主动接入——设备换 IP、重启后自动重连握手，HA 侧零干预
- **断线自愈**：死链看门狗自动清理半开连接，实体在设备离线时自动置为「不可用」，重连后自动恢复
- **设备端操作实时同步**：在 PDU 自己的管理界面上开关插座，HA 状态实时跟随
- **协议容错**：粘包/分包/噪声数据分帧容忍，控制帧带 ASCII 校验和，轮询协程带并发锁与硬超时，单次卡死不影响后续轮询
- **UI 配置**：监听端口、轮询间隔、插座位序全部可在集成选项中调整
- **零依赖**：纯 Python 标准库，无额外安装要求

## 支持设备

任何使用 `START ... END` 文本帧协议的 8 路 PDU（登录帧 `START login ...`、状态帧 `iostate`、查询帧 `PVC_get`/`PVC_Info`）。

## 安装

### 方式一：HACS（推荐）

1. HACS → ⋮ → 自定义仓库
2. 仓库填 `https://github.com/chaosl1996/smart_pdu`，类别选 **集成 (Integration)**
3. 搜索 **Smart PDU** 并安装
4. 重启 Home Assistant

### 方式二：手动安装

```bash
cd <HA配置目录>/custom_components
git clone https://github.com/chaosl1996/smart_pdu
cp -r smart_pdu/custom_components/smart_pdu .
```

然后重启 Home Assistant。

## 快速上手

1. 在 PDU 设备的管理界面中，将**服务器地址**配置为 HA 主机的 IP，端口 `4600`
2. **设置 → 设备与服务 → 添加集成**，搜索 **Smart PDU**，端口保持默认 `4600`
3. PDU 连入后自动完成握手，实体自动出现并开始上报

> 连接方向为 **PDU → HA**（PDU 是客户端）。PDU 自己的 IP 随便换（DHCP 重新分配也不影响），HA 侧不需要任何配置；但 **HA 的 IP 建议固定**（静态 IP 或路由器 DHCP 静态租约）。

## 实体

| 实体 | 说明 |
|---|---|
| `switch.smart_pdu_outlet_1` ~ `outlet_8` | 8 路插座开关 |
| `sensor.smart_pdu_power` | 总功率 (W) |
| `sensor.smart_pdu_voltage` | 电压 (V) |
| `sensor.smart_pdu_current` | 总电流 (A) |
| `sensor.smart_pdu_energy` | 累计电能 (kWh) |
| `sensor.smart_pdu_outlet_N_current` | 分插座电流 (A)，N = 1~8 |

任意 smart_pdu 实体的属性中可查看 `pdu_clients`（当前接入的设备地址）。

## 配置项

| 配置 | 说明 | 默认 |
|---|---|---|
| 监听端口 | HA 接收 PDU 连接的 TCP 端口 | `4600` |
| 轮询间隔 | PVC_get 电参数查询周期（秒） | `60` |
| 插座位序 | `iostate` 位掩码的位序：LSB（io8 bit0 = 插座1）或 MSB（bit7 = 插座1） | `LSB` |

修改选项后需**重载集成**生效（设置 → 设备与服务 → Smart PDU → ⋮ → 重新加载）。

## 工作原理（协议简述）

- 所有帧为 ASCII 文本，以 `START` 开头、`END` 结尾，控制帧带 `check` 校验（帧内字符 ASCII 求和 & 0xFF）
- **握手**：设备接入后发送 `START login ...`，HA 回复 `Login Successful`，设备随即推送 `iostate`（8 路开关位掩码）
- **轮询**：HA 按配置周期发送 `PVC_get`，设备回应 `PVC_Info`（电压/电流/功率/电能，v/c/c0~c7 需 ÷100）
- **控制**：HA 发送 `open/close io='<掩码>'`，设备回显确认；设备端界面操作则以插座序号实时推送
- **看门狗**：连接 180 秒无任何数据即判定死链并主动断开，促使设备重连

## 常见问题

**设备一直显示「未连接」？**
依次检查：① PDU 里配置的服务器地址是否指向 HA 主机 IP；② HA 主机 4600 端口是否被占用（如旧版 Node-RED 流程还在跑，先停掉）；③ 防火墙/端口映射；④ 设备反复异常断线后可能假死，**给 PDU 断电重启一次**即可恢复。

**开关和实际插座对不上？**
在集成选项中切换「插座位序」（LSB ↔ MSB）后重载集成。

**可以多台 HA 同时接入一台 PDU 吗？**
不可以——PDU 是 TCP 客户端，同一时刻只会连接它配置的那一个服务器。多端消费需求建议由已接入的 HA 通过 MQTT 转发。

**在 PDU 客户端上操作开关，HA 状态会变吗？**
会。设备会主动推送操作结果，HA 实时跟随（v2.1.0+）。
