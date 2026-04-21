"""WebSocket client for the Ksenia Lares 4.0 control panel.

Implements the documented JSON/WebSocket protocol used by the official
Ksenia SDK clients (SecureWeb, Ksenia Pro, Control4/Milestone integrations).
The wire protocol is line-oriented JSON over a WebSocket sub-protocol named
``KS_WSOCK`` reachable at ``/KseniaWsock``.

Only the pieces required by the Home Assistant gateway are implemented:

* Authentication (``LOGIN`` / ``LOGOUT``) with PIN.
* Reading static configuration (``READ`` / ``MULTI_TYPES``).
* Registering for real-time broadcasts (``REALTIME`` / ``REGISTER``).
* Executing automation scenarios (``CMD_USR`` / ``CMD_EXE_SCENARIO``).
* Retrieving the firmware version (``SYSTEM_VERSION``).

The client is fully asynchronous. A background listener task dispatches
incoming ``REALTIME`` frames to registered callbacks and resolves the
futures associated with outstanding request IDs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.typing import Subprotocol

from .const import READ_TYPES, REALTIME_TYPES
from .crc import add_crc

_LOGGER = logging.getLogger(__name__)

SUBPROTOCOL = Subprotocol("KS_WSOCK")
WEBSOCKET_PATH = "/KseniaWsock"
PING_INTERVAL = 30
REQUEST_TIMEOUT = 15
COMMAND_TIMEOUT = 30
RECONNECT_BASE_DELAY = 2.0
RECONNECT_MAX_DELAY = 60.0

RealtimeHandler = Callable[[str, list[dict[str, Any]]], Awaitable[None] | None]


class LaresError(Exception):
    """Base class for Ksenia Lares gateway errors."""


class LaresAuthError(LaresError):
    """Raised when the panel rejects the supplied credentials."""


class LaresConnectionError(LaresError):
    """Raised for network/WebSocket level failures."""


@dataclass
class _Pending:
    """Book-keeping for an in-flight request waiting on a response."""

    cmd: str
    payload_type: str
    future: asyncio.Future[dict[str, Any]]
    created_at: float = field(default_factory=time.monotonic)


class LaresClient:
    """Asynchronous WebSocket client for a Ksenia Lares 4.0 panel.

    The client takes care of:

    * Opening/closing the WebSocket (plain or TLS).
    * Logging in with PIN and keeping the session alive.
    * Fetching the initial configuration and status snapshot.
    * Subscribing to real-time state broadcasts.
    * Serializing outgoing commands (one inflight request id per message).
    * Automatic reconnection with exponential backoff.

    Callers interact with the client via:

    * :meth:`start` — open the connection and begin background tasks.
    * :meth:`stop` — cleanly shut everything down.
    * :meth:`add_realtime_listener` — receive updates for a given status type.
    * :meth:`execute_scenario` — trigger a scenario by ID.
    * :meth:`get_snapshot` / :meth:`get_system_info` — inspect cached data.
    """

    def __init__(
        self,
        host: str,
        pin: str,
        *,
        port: int | None = None,
        use_ssl: bool = True,
        username: str | None = None,
        sender: str = "HomeAssistant",
    ) -> None:
        self._host = host
        self._pin = str(pin)
        self._username = username
        self._use_ssl = use_ssl
        self._port = port if port is not None else (443 if use_ssl else 80)
        self._sender = sender

        self._ws: websockets.ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._login_id: int = -1
        self._msg_id = 0
        self._pending: dict[str, _Pending] = {}
        self._listeners: dict[str, list[RealtimeHandler]] = {}
        self._connection_callbacks: list[Callable[[bool], Awaitable[None] | None]] = []
        self._snapshot: dict[str, list[dict[str, Any]]] = {}
        self._system_info: dict[str, Any] = {}
        self._ready = asyncio.Event()
        self._listener_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._running = False
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def use_ssl(self) -> bool:
        return self._use_ssl

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def system_info(self) -> dict[str, Any]:
        return dict(self._system_info)

    @property
    def base_url(self) -> str:
        scheme = "https" if self._use_ssl else "http"
        return f"{scheme}://{self._host}:{self._port}"

    def get_snapshot(self, key: str) -> list[dict[str, Any]]:
        """Return the latest cached payload for *key* (or ``[]``)."""
        return list(self._snapshot.get(key, []))

    async def wait_ready(self, timeout: float) -> bool:
        """Wait until the initial snapshot has been fetched."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def add_realtime_listener(self, key: str, handler: RealtimeHandler) -> Callable[[], None]:
        """Subscribe *handler* to real-time updates of *key*.

        ``key`` is one of the ``STATUS_*`` tokens defined by the panel
        (e.g. ``STATUS_ZONES``). Returns a callable that unregisters the
        listener.
        """
        self._listeners.setdefault(key, []).append(handler)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners[key].remove(handler)

        return _unsubscribe

    def add_connection_listener(
        self, handler: Callable[[bool], Awaitable[None] | None]
    ) -> Callable[[], None]:
        """Subscribe *handler* to connected/disconnected transitions."""
        self._connection_callbacks.append(handler)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._connection_callbacks.remove(handler)

        return _unsubscribe

    async def start(self) -> None:
        """Connect once (raises on failure) and start the supervisor loop.

        Intended to be called from ``async_setup_entry``: the first
        connection attempt is synchronous so the caller can surface
        authentication/connection errors; once connected, a supervisor
        task keeps the link alive across network blips.
        """
        self._running = True
        await self._connect_and_prime()
        self._supervisor_task = asyncio.create_task(
            self._supervise(), name="ksenia_lares4.supervisor"
        )

    async def stop(self) -> None:
        """Close the connection and cancel background tasks."""
        self._running = False
        self._ready.clear()

        for task in (self._supervisor_task, self._listener_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(Exception):
                    await task
        self._supervisor_task = None
        self._listener_task = None

        await self._shutdown_session(notify=True)

    async def execute_scenario(self, scenario_id: str | int, pin: str | None = None) -> bool:
        """Run the scenario with ``ID == scenario_id``.

        When *pin* is ``None`` the PIN supplied at construction time is
        used. Returning ``True`` means the panel acknowledged the request
        (``RESULT == "OK"``).
        """
        payload = {
            "ID_LOGIN": str(self._login_id),
            "PIN": str(pin if pin is not None else self._pin),
            "SCENARIO": {"ID": str(scenario_id)},
        }
        try:
            response = await self._send_and_wait(
                "CMD_USR", "CMD_EXE_SCENARIO", payload, timeout=COMMAND_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise LaresError("Scenario execution timed out") from err
        result = response.get("PAYLOAD", {}).get("RESULT")
        detail = response.get("PAYLOAD", {}).get("RESULT_DETAIL")
        if result != "OK":
            _LOGGER.warning(
                "Scenario %s rejected by panel: result=%s detail=%s",
                scenario_id,
                result,
                detail,
            )
            if detail == "WRONG_PIN":
                raise LaresAuthError("PIN rejected by panel")
            return False
        return True

    async def refresh_snapshot(self) -> None:
        """Re-fetch the full static + status snapshot."""
        await self._fetch_snapshot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._msg_id = (self._msg_id + 1) & 0xFFFF
        if self._msg_id == 0:
            self._msg_id = 1
        return str(self._msg_id)

    def _build_message(
        self,
        cmd: str,
        payload_type: str,
        payload: dict[str, Any],
        msg_id: str,
    ) -> str:
        envelope = {
            "SENDER": self._sender,
            "RECEIVER": "",
            "CMD": cmd,
            "ID": msg_id,
            "PAYLOAD_TYPE": payload_type,
            "PAYLOAD": payload,
            "TIMESTAMP": str(int(time.time())),
            "CRC_16": "0x0000",
        }
        return add_crc(json.dumps(envelope, separators=(",", ":")))

    async def _send_raw(self, message: str) -> None:
        if self._ws is None:
            raise LaresConnectionError("WebSocket not connected")
        async with self._send_lock:
            await self._ws.send(message)

    async def _send_and_wait(
        self,
        cmd: str,
        payload_type: str,
        payload: dict[str, Any],
        *,
        timeout: float = REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a request and wait for the matching response frame."""
        msg_id = self._next_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = _Pending(cmd=cmd, payload_type=payload_type, future=future)
        message = self._build_message(cmd, payload_type, payload, msg_id)
        try:
            await self._send_raw(message)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    # -- Connection lifecycle ------------------------------------------------

    async def _connect_and_prime(self) -> None:
        """Open the WebSocket, log in, subscribe and fetch the snapshot."""
        uri = f"{'wss' if self._use_ssl else 'ws'}://{self._host}:{self._port}{WEBSOCKET_PATH}"
        ssl_ctx: ssl.SSLContext | None = None
        if self._use_ssl:
            # Ksenia panels ship legacy TLS 1.2 certs with renegotiation
            # disabled server-side; matching the settings used by the
            # reference driver/SDK yields the most reliable handshake.
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            try:
                ssl_ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT  # type: ignore[attr-defined]
            except AttributeError:  # pragma: no cover - older Pythons
                ssl_ctx.options |= 0x4
            # Some panels use very short RSA keys; relax cipher rules so
            # OpenSSL 3 doesn't drop the connection pre-handshake.
            try:
                ssl_ctx.set_ciphers("DEFAULT@SECLEVEL=0")
            except ssl.SSLError:
                _LOGGER.debug("Could not relax cipher level, using defaults")

        _LOGGER.debug("Connecting to Ksenia panel at %s", uri)
        try:
            self._ws = await ws_connect(
                uri,
                ssl=ssl_ctx,
                subprotocols=[SUBPROTOCOL],
                ping_interval=PING_INTERVAL,
                # Some panels never reply to WebSocket pings; don't close
                # the socket when that happens.
                ping_timeout=None,
                open_timeout=15,
                max_size=4 * 1024 * 1024,
            )
        except asyncio.TimeoutError as err:
            raise LaresConnectionError(
                f"Timed out opening WebSocket to {uri}"
            ) from err
        except (
            OSError,
            ssl.SSLError,
            websockets.InvalidURI,
            websockets.InvalidHandshake,
            websockets.WebSocketException,
        ) as err:
            raise LaresConnectionError(f"Could not connect to {uri}: {err}") from err
        except Exception as err:  # pragma: no cover - defensive
            # Anything else (import errors, TypeErrors from the ws lib
            # version mismatch, …) becomes a connection error so the
            # caller can distinguish it from auth failures.
            raise LaresConnectionError(
                f"Unexpected error opening WebSocket to {uri}: {err!r}"
            ) from err

        # Authentication is the first frame on a new socket; do it before
        # starting the listener so we can observe the LOGIN_RES inline.
        login_id = await self._authenticate()
        self._login_id = login_id
        self._connected = True
        _LOGGER.info(
            "Authenticated with Ksenia Lares 4.0 at %s (session id=%s)",
            self._host,
            login_id,
        )

        # Start the listener before issuing any further requests so
        # it can resolve pending futures and dispatch realtime frames.
        self._listener_task = asyncio.create_task(
            self._listen_loop(), name="ksenia_lares4.listener"
        )

        await self._fetch_system_version()
        await self._register_realtime()
        await self._fetch_snapshot()
        self._ready.set()

        await self._notify_connection(True)

    async def _authenticate(self) -> int:
        assert self._ws is not None
        msg_id = self._next_id()
        envelope = self._build_message("LOGIN", "USER", {"PIN": self._pin}, msg_id)
        await self._ws.send(envelope)
        deadline = time.monotonic() + REQUEST_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaresConnectionError("Login timed out")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError as err:
                raise LaresConnectionError("Login timed out") from err
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if response.get("CMD") not in ("LOGIN", "LOGIN_RES"):
                # Unexpected pre-login frame — discard and keep waiting.
                continue
            payload = response.get("PAYLOAD", {})
            if payload.get("RESULT") != "OK":
                raise LaresAuthError(
                    payload.get("RESULT_DETAIL") or "PIN rejected by the panel"
                )
            try:
                return int(payload["ID_LOGIN"])
            except (KeyError, TypeError, ValueError) as err:
                raise LaresConnectionError("Malformed LOGIN response") from err

    async def _fetch_system_version(self) -> None:
        try:
            response = await self._send_and_wait(
                "SYSTEM_VERSION",
                "REQUEST",
                {"ID_LOGIN": str(self._login_id)},
                timeout=REQUEST_TIMEOUT,
            )
        except (asyncio.TimeoutError, LaresError):
            _LOGGER.debug("Could not fetch system version, continuing without it")
            self._system_info = {}
            return
        payload = response.get("PAYLOAD", {})
        if payload.get("RESULT") == "OK":
            self._system_info = {k: v for k, v in payload.items() if k != "RESULT"}

    async def _register_realtime(self) -> None:
        payload = {
            "ID_LOGIN": str(self._login_id),
            "TYPES": REALTIME_TYPES,
        }
        try:
            response = await self._send_and_wait(
                "REALTIME", "REGISTER", payload, timeout=REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise LaresConnectionError("Real-time subscription timed out") from err
        # The initial real-time frame usually carries a snapshot of all
        # subscribed types — merge it into our cache.
        rt_payload = response.get("PAYLOAD", {})
        if isinstance(rt_payload, dict):
            nested = rt_payload.get(self._sender)
            if isinstance(nested, dict):
                rt_payload = nested
            for key, value in rt_payload.items():
                if isinstance(value, list):
                    self._snapshot[key] = value

    async def _fetch_snapshot(self) -> None:
        read_id = self._next_id()
        payload = {
            "ID_LOGIN": str(self._login_id),
            "ID_READ": read_id,
            "TYPES": READ_TYPES,
        }
        # READ shares the same id field as the outer envelope.
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[read_id] = _Pending(cmd="READ", payload_type="MULTI_TYPES", future=future)
        message = self._build_message("READ", "MULTI_TYPES", payload, read_id)
        try:
            await self._send_raw(message)
            response = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
        except asyncio.TimeoutError as err:
            self._pending.pop(read_id, None)
            raise LaresConnectionError("Read snapshot timed out") from err
        finally:
            self._pending.pop(read_id, None)
        payload_data = response.get("PAYLOAD", {})
        if not isinstance(payload_data, dict):
            return
        for key, value in payload_data.items():
            if isinstance(value, list):
                self._snapshot[key] = value

    # -- Listener ------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Dispatch incoming frames until the socket is closed."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    _LOGGER.debug("Dropping malformed frame: %r", raw)
                    continue
                await self._dispatch(msg)
        except websockets.ConnectionClosed as err:
            _LOGGER.info("WebSocket closed: %s", err)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            _LOGGER.exception("Listener crashed")
        finally:
            # Fail any outstanding waiters and flag the disconnect.
            for pending in list(self._pending.values()):
                if not pending.future.done():
                    pending.future.set_exception(
                        LaresConnectionError("WebSocket closed before response")
                    )
            self._pending.clear()
            if self._connected:
                self._connected = False
                await self._notify_connection(False)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = str(msg.get("ID", ""))
        cmd = msg.get("CMD", "")
        payload_type = msg.get("PAYLOAD_TYPE", "")
        payload = msg.get("PAYLOAD", {})

        # Resolve a pending request by ID.
        pending = self._pending.pop(msg_id, None) if msg_id else None
        if pending is None and msg_id:
            # Tolerate occasional ID mismatches by matching on (CMD, PAYLOAD_TYPE).
            for pid, pend in list(self._pending.items()):
                if pend.cmd == cmd and pend.payload_type == payload_type:
                    pending = self._pending.pop(pid)
                    break
        if pending is not None and not pending.future.done():
            pending.future.set_result(msg)
            # For REALTIME REGISTER responses the payload already contains
            # an initial snapshot; also forward it to listeners.
            if cmd == "REALTIME":
                await self._forward_realtime(payload)
            return

        if cmd == "REALTIME":
            await self._forward_realtime(payload)
            return

        _LOGGER.debug("Unhandled frame: cmd=%s payload_type=%s id=%s", cmd, payload_type, msg_id)

    async def _forward_realtime(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        # Panels sometimes wrap real-time deltas inside ``{sender: {...}}``
        nested = payload.get(self._sender)
        if isinstance(nested, dict):
            payload = nested
        for key, value in payload.items():
            if not isinstance(value, list):
                continue
            self._snapshot[key] = value
            for handler in list(self._listeners.get(key, [])):
                try:
                    result = handler(key, value)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception("Realtime listener for %s raised", key)

    async def _notify_connection(self, is_connected: bool) -> None:
        for callback in list(self._connection_callbacks):
            try:
                result = callback(is_connected)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Connection callback raised")

    # -- Supervisor ---------------------------------------------------------

    async def _supervise(self) -> None:
        """Keep the WebSocket alive with exponential backoff."""
        backoff = RECONNECT_BASE_DELAY
        while self._running:
            if self._listener_task is not None:
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    return
                except Exception:  # pragma: no cover - defensive
                    _LOGGER.exception("Listener task exited with error")
            self._listener_task = None
            if not self._running:
                return
            await self._shutdown_session(notify=False)

            _LOGGER.warning(
                "Connection to Ksenia panel lost, reconnecting in %.1fs", backoff
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                await self._connect_and_prime()
            except LaresAuthError:
                _LOGGER.error("Authentication failed — stopping reconnection attempts")
                self._running = False
                return
            except LaresError as err:
                _LOGGER.warning("Reconnect attempt failed: %s", err)
                backoff = min(backoff * 2, RECONNECT_MAX_DELAY)
                continue
            backoff = RECONNECT_BASE_DELAY

    async def _shutdown_session(self, *, notify: bool) -> None:
        """Tear down the current session (used on stop and before reconnect)."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            with contextlib.suppress(Exception):
                await self._listener_task
        self._listener_task = None

        ws = self._ws
        self._ws = None
        if ws is not None and not ws.state.name.startswith("CLOS"):
            if self._login_id >= 0:
                with contextlib.suppress(Exception):
                    await ws.send(
                        self._build_message(
                            "LOGOUT",
                            "USER",
                            {"ID_LOGIN": str(self._login_id)},
                            self._next_id(),
                        )
                    )
            with contextlib.suppress(Exception):
                await ws.close()
        self._login_id = -1

        was_connected = self._connected
        self._connected = False
        self._ready.clear()
        if notify and was_connected:
            await self._notify_connection(False)
