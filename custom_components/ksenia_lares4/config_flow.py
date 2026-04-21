"""Config flow for the Ksenia Lares 4.0 Gateway integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .api import LaresAuthError, LaresClient, LaresConnectionError
from .const import (
    CONF_PIN,
    CONF_SSL,
    CONF_USERNAME,
    DEFAULT_PORT_PLAIN,
    DEFAULT_PORT_SSL,
    DEFAULT_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _parse_url(raw: str) -> tuple[str, int, bool]:
    """Parse a user-provided URL or host string.

    Accepts values like ``https://192.168.1.10``, ``https://lares:443``,
    ``192.168.1.10`` (SSL assumed) or ``http://lares:80``.

    Returns ``(host, port, use_ssl)``.
    """
    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.hostname:
        raise ValueError("missing host")
    use_ssl = parsed.scheme.lower() in ("https", "wss")
    default_port = DEFAULT_PORT_SSL if use_ssl else DEFAULT_PORT_PLAIN
    port = parsed.port or default_port
    return parsed.hostname, port, use_ssl


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                "url",
                default=defaults.get("url", "https://"),
            ): str,
            vol.Optional(
                CONF_USERNAME,
                default=defaults.get(CONF_USERNAME, ""),
            ): str,
            vol.Required(CONF_PIN, default=defaults.get(CONF_PIN, "")): str,
        }
    )


class LaresConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration of a Ksenia Lares 4.0 gateway."""

    VERSION = 1

    def __init__(self) -> None:
        self._defaults: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host, port, use_ssl = _parse_url(user_input["url"])
            except ValueError:
                errors["url"] = "invalid_url"
            else:
                entry_data = {
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_SSL: use_ssl,
                    CONF_USERNAME: user_input.get(CONF_USERNAME) or None,
                    CONF_PIN: user_input[CONF_PIN],
                }
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured(updates=entry_data)

                error = await _async_test_connection(entry_data)
                if error is None:
                    title = user_input.get(CONF_USERNAME) or host
                    return self.async_create_entry(
                        title=f"Ksenia Lares 4.0 ({title})",
                        data=entry_data,
                    )
                errors["base"] = error
                self._defaults = {
                    "url": user_input["url"],
                    CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                    CONF_PIN: user_input.get(CONF_PIN, ""),
                }

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(self._defaults),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> FlowResult:
        self._defaults = {}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            new_data = {**entry.data, CONF_PIN: user_input[CONF_PIN]}
            if user_input.get(CONF_USERNAME):
                new_data[CONF_USERNAME] = user_input[CONF_USERNAME]
            error = await _async_test_connection(new_data)
            if error is None:
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_USERNAME,
                        default=entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PIN): str,
                }
            ),
            errors=errors,
        )

    def _get_reauth_entry(self) -> config_entries.ConfigEntry:
        entry_id = self.context.get("entry_id", "")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        assert entry is not None
        return entry


async def _async_test_connection(data: dict[str, Any]) -> str | None:
    """Attempt a throwaway connection and return an error slug (or ``None``)."""
    _LOGGER.debug(
        "Testing connection to Ksenia panel host=%s port=%s ssl=%s",
        data.get(CONF_HOST),
        data.get(CONF_PORT),
        data.get(CONF_SSL, DEFAULT_SSL),
    )
    client = LaresClient(
        host=data[CONF_HOST],
        pin=data[CONF_PIN],
        port=data.get(CONF_PORT),
        use_ssl=data.get(CONF_SSL, DEFAULT_SSL),
        username=data.get(CONF_USERNAME),
    )
    try:
        await client.start()
        if not await client.wait_ready(timeout=25):
            _LOGGER.warning(
                "Connection test: connected but initial snapshot timed out"
            )
            return "timeout"
    except LaresAuthError as err:
        _LOGGER.warning("Connection test: authentication rejected (%s)", err)
        return "invalid_auth"
    except LaresConnectionError as err:
        _LOGGER.warning("Connection test: cannot connect (%s)", err)
        return "cannot_connect"
    except asyncio.TimeoutError:
        _LOGGER.warning("Connection test: timed out")
        return "timeout"
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.exception(
            "Connection test: unexpected %s — %s", type(err).__name__, err
        )
        return "cannot_connect"
    finally:
        try:
            await client.stop()
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("Failed to cleanly stop test client", exc_info=True)
    return None
