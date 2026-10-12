"""Constants for the Smart PDU integration."""

DOMAIN = "smart_pdu"

CONF_PORT = "port"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_BIT_ORDER = "outlet_bit_order"

DEFAULT_PORT = 4600
DEFAULT_UPDATE_INTERVAL = 60
DEFAULT_BIT_ORDER = "lsb"

BIT_ORDER_LSB = "lsb"  # bit1(=1)   -> 插座1, 与 flows.json 中开关节点 1/2/4/.../128 一致
BIT_ORDER_MSB = "msb"  # bit8(=128) -> 插座1

NUM_OUTLETS = 8

# 设备发送 START login ... 后等待的回复内容（与 Node-RED 流程完全一致）
LOGIN_REPLY = "Login Successful"

# PVC_get 发出后等待 PVC_Info 的超时时间
POLL_TIMEOUT = 10
# 连接超过该秒数无任何数据则判定为死链并踢掉（让设备重新连接触发握手）
STALE_AFTER = 180

CMD_PVC_GET = "PVC_get"
