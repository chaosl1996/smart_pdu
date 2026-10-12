"""Config flow for Smart PDU."""

from __future__ import annotations

import socket
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    BIT_ORDER_LSB,
    BIT_ORDER_MSB,
    CONF_BIT_ORDER,
    CONF_PORT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_BIT_ORDER,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

BIT_ORDER_LABELS = {
    BIT_ORDER_LSB: "LSB（插座1 = 最低位 bit0，推荐）",
    BIT_ORDER_MSB: "MSB（插座1 = 最高位 bit7）",
}


def _base_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): int,
            vol.Required(
                CONF_BIT_ORDER,
                default=defaults.get(CONF_BIT_ORDER, DEFAULT_BIT_ORDER),
            ): vol.In(BIT_ORDER_LABELS),
        }
    )


async def validate_port(hass: HomeAssistant, port: int) -> None:
    """检查端口可绑定（例如 Node-RED 仍占用 4600 时给出明确错误）。"""

    def _check() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
        except OSError as err:
            raise PortInUse(f"Port {port} unavailable: {err}") from err
        finally:
            sock.close()

    await hass.async_add_executor_job(_check)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await validate_port(self.hass, user_input[CONF_PORT])
            except PortInUse:
                errors["base"] = "port_in_use"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{DOMAIN}-{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Smart PDU (port {user_input[CONF_PORT]})", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_base_schema({}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_base_schema(defaults))


class PortInUse(HomeAssistantError):
    """端口无法绑定。"""
