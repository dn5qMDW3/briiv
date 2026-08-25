"""Client for the Briiv cloud API.

Briiv devices report to AWS as well as broadcasting on the local network. The
phone app reaches them through a Cognito login and an API Gateway WebSocket;
this module speaks the same protocol so the integration can work away from
home, or where the device's broadcasts cannot reach Home Assistant.

The protocol is documented in tools/cloud-api.md. In short:

  * log in against a Cognito user pool with a passwordless email code, keeping
    the refresh token so later sessions need no new code
  * open a WebSocket with the resulting ID token in the query string
  * exchange JSON messages: ``fetchDevices`` to read, ``updateDevice`` to
    control, and pushed ``device``/``devices`` messages for live updates
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import json
from typing import Any

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER

COGNITO_URL = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID = "336gl87kpsv161e6kp6jdc6a3g"
WEBSOCKET_URL = "wss://nzp7wg4kbl.execute-api.eu-west-1.amazonaws.com/Prod/"

_JSON_CONTENT_TYPE = "application/x-amz-json-1.1"
_TARGET = "AWSCognitoIdentityProviderService"

REQUEST_TIMEOUT = 30
# The server sends nothing while a device is idle, so ping to spot dead links.
HEARTBEAT = 30
RECONNECT_DELAYS = (5, 15, 30, 60, 120)

type DeviceCallback = Callable[[dict[str, dict[str, Any]]], None]


class BriivCloudError(HomeAssistantError):
    """Raised when the cloud API cannot be reached or returns an error."""


class BriivCloudAuthError(BriivCloudError):
    """Raised when credentials are rejected and the user must log in again."""


async def _cognito_call(
    session: aiohttp.ClientSession, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Call the Cognito identity provider and return the decoded response."""
    try:
        response = await session.post(
            COGNITO_URL,
            data=json.dumps(payload),
            headers={
                "Content-Type": _JSON_CONTENT_TYPE,
                "X-Amz-Target": f"{_TARGET}.{action}",
            },
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        )
        body = await response.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as err:
        raise BriivCloudError(
            f"Could not reach the Briiv account service: {err}"
        ) from err

    if response.status != 200:
        message = body.get("message", "unknown error") if isinstance(body, dict) else ""
        kind = body.get("__type", "") if isinstance(body, dict) else ""
        if "NotAuthorized" in kind or "UserNotFound" in kind or "ExpiredCode" in kind:
            raise BriivCloudAuthError(message or kind)
        raise BriivCloudError(message or f"Cognito returned {response.status}")

    return body


async def async_request_code(
    session: aiohttp.ClientSession, email: str
) -> tuple[str, str]:
    """Ask Briiv to email a sign-in code.

    Returns the Cognito session handle and the username to answer it with. The
    handle is only valid for a few minutes, so the code must be submitted soon.
    """
    body = await _cognito_call(
        session,
        "InitiateAuth",
        {
            "AuthFlow": "CUSTOM_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"USERNAME": email},
            "ClientMetadata": {},
        },
    )

    challenge_session = body.get("Session")
    username = body.get("ChallengeParameters", {}).get("USERNAME")
    if not challenge_session or not username:
        raise BriivCloudError("The account service did not start a sign-in challenge")

    return challenge_session, username


async def async_submit_code(
    session: aiohttp.ClientSession,
    challenge_session: str,
    username: str,
    code: str,
) -> dict[str, Any]:
    """Answer the emailed code and return the resulting tokens."""
    body = await _cognito_call(
        session,
        "RespondToAuthChallenge",
        {
            "ChallengeName": "CUSTOM_CHALLENGE",
            "ClientId": COGNITO_CLIENT_ID,
            "Session": challenge_session,
            "ChallengeResponses": {"USERNAME": username, "ANSWER": code},
        },
    )

    result = body.get("AuthenticationResult")
    if not result:
        # Cognito answers a wrong code with another challenge rather than an error.
        raise BriivCloudAuthError("That code was not accepted")

    return result


async def async_refresh_tokens(
    session: aiohttp.ClientSession, refresh_token: str
) -> dict[str, Any]:
    """Exchange a stored refresh token for fresh tokens, without a new code."""
    body = await _cognito_call(
        session,
        "InitiateAuth",
        {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
        },
    )

    result = body.get("AuthenticationResult")
    if not result:
        raise BriivCloudAuthError("The stored sign-in has expired")

    return result


def device_id(device: dict[str, Any]) -> str | None:
    """Return a device's serial number.

    The cloud payload has not been fully catalogued, so accept the spellings a
    device identifier plausibly uses rather than depending on just one.
    """
    for key in ("id", "serialNumber", "serial_number", "thingName", "Serial"):
        value = device.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class BriivCloudAPI:
    """Maintains the WebSocket session that carries device state and commands."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        on_token_rotated: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the client with a stored refresh token.

        ``on_token_rotated`` is called when the service issues a replacement
        refresh token, so it can be written back to the config entry; losing it
        would mean asking the user for a new email code.
        """
        self._session = session
        self._refresh_token = refresh_token
        self._on_token_rotated = on_token_rotated
        # Set by the coordinator so an expired sign-in can ask the user for a
        # new code instead of the connection quietly giving up.
        self.on_auth_failed: Callable[[], None] | None = None
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._listener: asyncio.Task[None] | None = None
        self._callbacks: list[DeviceCallback] = []
        self._closing = False

        self.devices: dict[str, dict[str, Any]] = {}

    @property
    def refresh_token(self) -> str:
        """Return the current refresh token."""
        return self._refresh_token

    @property
    def connected(self) -> bool:
        """Return whether the WebSocket is currently usable."""
        return self._socket is not None and not self._socket.closed

    def register_callback(self, callback: DeviceCallback) -> Callable[[], None]:
        """Register a listener for device updates and return an unsubscriber."""
        self._callbacks.append(callback)

        def _unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        """Hand the current device set to every listener."""
        for callback in list(self._callbacks):
            callback(self.devices)

    async def _async_open_socket(self) -> aiohttp.ClientWebSocketResponse:
        """Refresh the sign-in and open a WebSocket with the new token."""
        tokens = await async_refresh_tokens(self._session, self._refresh_token)
        # Cognito only returns a new refresh token when it rotates one.
        rotated = tokens.get("RefreshToken")
        if rotated and rotated != self._refresh_token:
            self._refresh_token = rotated
            if self._on_token_rotated:
                self._on_token_rotated(rotated)

        id_token = tokens.get("IdToken")
        if not id_token:
            raise BriivCloudAuthError("The account service returned no ID token")

        try:
            return await self._session.ws_connect(
                WEBSOCKET_URL,
                params={"token": id_token},
                heartbeat=HEARTBEAT,
                timeout=aiohttp.ClientWSTimeout(ws_close=REQUEST_TIMEOUT),
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise BriivCloudAuthError(
                    "The Briiv account rejected the sign-in"
                ) from err
            raise BriivCloudError(
                f"Could not open the Briiv connection: {err}"
            ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BriivCloudError(
                f"Could not open the Briiv connection: {err}"
            ) from err

    async def async_connect(self) -> None:
        """Connect, read the current devices, and keep the session alive."""
        self._closing = False
        self._socket = await self._async_open_socket()
        await self._async_send({"action": "fetchDevices"})
        self._listener = asyncio.create_task(self._async_listen())

    async def _async_send(self, message: dict[str, Any]) -> None:
        """Send one message, failing clearly when the link is down."""
        socket = self._socket
        if socket is None or socket.closed:
            raise BriivCloudError("Not connected to the Briiv service")

        try:
            await socket.send_str(json.dumps(message))
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BriivCloudError(
                f"Could not send to the Briiv service: {err}"
            ) from err

    def _handle_message(self, payload: dict[str, Any]) -> None:
        """Fold an incoming message into the cached device set."""
        kind = payload.get("type")

        if kind == "devices":
            for device in payload.get("devices") or []:
                if serial := device_id(device):
                    self.devices[serial] = device
        elif kind == "device":
            device = payload.get("device") or {}
            if serial := device_id(device):
                self.devices[serial] = device
        else:
            LOGGER.debug("Ignoring unknown cloud message type %s", kind)
            return

        self._notify()

    async def _async_listen(self) -> None:
        """Read messages until the socket closes, then reconnect."""
        attempt = 0
        while not self._closing:
            try:
                if self._socket is None:
                    return
                async for message in self._socket:
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        self._handle_message(json.loads(message.data))
                    except json.JSONDecodeError:
                        LOGGER.debug("Ignoring malformed cloud message")
                    attempt = 0
            except asyncio.CancelledError:
                raise
            except (TimeoutError, aiohttp.ClientError) as err:
                LOGGER.debug("Briiv cloud connection dropped: %s", err)

            if self._closing:
                return

            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            LOGGER.debug("Reconnecting to the Briiv service in %ss", delay)
            await asyncio.sleep(delay)

            try:
                self._socket = await self._async_open_socket()
                await self._async_send({"action": "fetchDevices"})
            except BriivCloudAuthError:
                # Retrying cannot help; the user has to sign in again.
                LOGGER.warning("Briiv sign-in expired; reauthentication required")
                self._closing = True
                if self.on_auth_failed:
                    self.on_auth_failed()
                return
            except BriivCloudError as err:
                LOGGER.debug("Reconnect failed: %s", err)

    async def async_refresh_devices(self) -> None:
        """Ask the service to resend the current device list."""
        await self._async_send({"action": "fetchDevices"})

    async def async_update_device(self, serial: str, state: dict[str, Any]) -> None:
        """Change settings on one device."""
        await self._async_send({"action": "updateDevice", "id": serial, "state": state})

    async def async_disconnect(self) -> None:
        """Close the session and stop reconnecting."""
        self._closing = True

        if self._listener and not self._listener.done():
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        self._listener = None

        if self._socket and not self._socket.closed:
            await self._socket.close()
        self._socket = None
