# Briiv cloud API — reverse-engineering notes

Research notes for a possible cloud/MQTT transport in the Home Assistant
integration, to complement the local UDP path. Captured by intercepting the
`com.fivecreate.briiv2` Android app (v2.2.19) with mitmproxy on a rooted
emulator (see `mobile.md`) and by reading the device firmware (v2.3.32).

**Status: COMPLETE. Authentication and the full data transport (an API Gateway
WebSocket) are captured and specified end-to-end. There is no Cognito Identity
Pool and no direct AWS IoT access from the app — only the Cognito user-pool
JWT. See "Everything needed to build the cloud path" at the end.**

## Architecture

The Briiv firmware pushes device state to two independent places:

```
                    ┌─ UDP broadcast :3334 (LAN) ──→ Home Assistant (local path, shipped)
Briiv firmware ─────┤
                    └─ MQTT/TLS → AWS IoT (cloud) ─→ phone app (cloud path, this doc)
```

The two are independent: the local integration needs no internet, and the app
works away from home. Field names differ by transport — snake_case on the wire
locally (`fan_speed`, `boost_end_time`), camelCase in the cloud shadow
(`fanSpeed`, `boostEnd`).

## Confirmed configuration

| Item | Value |
|---|---|
| Region | `eu-west-1` |
| Cognito User Pool ID | `eu-west-1_Dp0BBIznz` |
| App Client ID | `336gl87kpsv161e6kp6jdc6a3g` (no client secret) |
| Auth flow | `CUSTOM_AUTH` — passwordless email OTP |
| AWS account | `455458493081` (from the user-pool KMS ARN) |
| IoT ATS endpoint | `a314yhhv532886-ats.iot.eu-west-1.amazonaws.com` (DNS CNAME of `iot.services.briiv.co.uk`) |
| Shadow topic (from firmware) | `$aws/things/{serial_number}/shadow/update` |
| Identity Pool | **None** — the app does not federate to a Cognito Identity Pool (confirmed by reading the app's token store after login: only user-pool tokens) |
| Data transport | **API Gateway WebSocket** `wss://nzp7wg4kbl.execute-api.eu-west-1.amazonaws.com/Prod/?token=<IdToken>` — JSON messages, Cognito ID token as query param. See "Data transport" below |

## Authentication flow (reproducible)

Passwordless: the user enters an email, receives a 6-digit code, and enters it.
No password is ever stored. Two Cognito calls:

### 1. InitiateAuth — request a code

```
POST https://cognito-idp.eu-west-1.amazonaws.com/
Content-Type: application/x-amz-json-1.1
X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth

{
  "AuthFlow": "CUSTOM_AUTH",
  "ClientId": "336gl87kpsv161e6kp6jdc6a3g",
  "AuthParameters": { "USERNAME": "<email>" },
  "ClientMetadata": {}
}
```

Response emails a code and returns:

```json
{
  "ChallengeName": "CUSTOM_CHALLENGE",
  "ChallengeParameters": { "USERNAME": "<cognito-sub-uuid>", "email": "<email>" },
  "Session": "<opaque session, ~3 min TTL>"
}
```

The `Session` is the binding constraint: it expires in roughly **3 minutes**,
so the code must be submitted promptly after it is requested.

### 2. RespondToAuthChallenge — submit the code

```
POST https://cognito-idp.eu-west-1.amazonaws.com/
X-Amz-Target: AWSCognitoIdentityProviderService.RespondToAuthChallenge

{
  "ChallengeName": "CUSTOM_CHALLENGE",
  "ClientId": "336gl87kpsv161e6kp6jdc6a3g",
  "Session": "<session from step 1>",
  "ChallengeResponses": {
    "USERNAME": "<cognito-sub-uuid from step 1>",
    "ANSWER": "<6-digit code>"
  }
}
```

Success returns `AuthenticationResult` with `IdToken`, `AccessToken`,
`RefreshToken` (JWTs). The ID token's `iss` reveals the user pool
(`.../eu-west-1_Dp0BBIznz`); `exp` is `iat + 3600` (1-hour access/ID token).

This was executed end-to-end successfully — the flow is correct.

### UX consequence for an integration

Because auth is passwordless, there is **no stored password** for silent
re-authentication. Home Assistant would depend on the Cognito **refresh token**
(typically ~30-day validity, pool-configurable). When it expires, the only way
back in is another emailed code entered by hand. Acceptable for a cloud
integration, but it is an inherent, recurring UX cost, not something the
integration can remove.

## Data transport — API Gateway WebSocket (SOLVED)

The app does **not** talk to AWS IoT directly. Device state and commands flow
over an **API Gateway WebSocket API**, captured by routing the emulator through
mitmproxy in WireGuard mode (the WS connection ignores the Android HTTP proxy;
WireGuard mode intercepts it transparently).

- **Endpoint:** `wss://nzp7wg4kbl.execute-api.eu-west-1.amazonaws.com/Prod/`
- **Auth:** the Cognito **ID token** is passed as a query parameter on the
  handshake: `wss://.../Prod/?token=<IdToken>`. An API Gateway Lambda authorizer
  validates it. **No Cognito Identity Pool and no direct IoT access are
  involved** — only the user-pool ID token from the auth flow above.
- The `iot.services.briiv.co.uk` endpoint and shadow topics are the
  *firmware→cloud* leg; the app never connects to them. The integration only
  needs this WebSocket.

### Messages (JSON, both directions)

Fetch all devices for the account:

```json
--> {"action":"fetchDevices"}
<-- {"type":"devices","devices":[ { ...device... }, ... ]}
```

Send a command to one device (addressed by serial number):

```json
--> {"action":"updateDevice","id":"BRI0000008338","state":{"fanSpeed":50}}
<-- {"type":"device","device":{ ...updated device... }}
```

`state` takes the same fields the cloud reports (e.g. `fanSpeed`, and by
analogy `boost`/`power`). The server also pushes `{"type":"device",...}` /
`{"type":"devices",...}` messages as state changes, so a subscribed client sees
live updates without polling.

### Device object (observed fields, partial)

```
Ip, "Link Code", "Free heap size",
fanSpeed, boostEnd,
Co  = "1060.2,1070.6,1052.5,..."   # CO2 in ppm, comma-joined per-sensor array
Al, Cm,                             # match firmware CM_ACTIVE etc.
coconutFilter = 95,                 # filter life %
coconutFilterChangeTimestamp, coconutFilterUsageMinutesRemaining, ...
```

The `Co` ~1060 ppm values confirm the local-path decision that this field is
**carbon dioxide**, not carbon monoxide. The cloud also exposes data the local
UDP broadcast does not (filter life, IP, link code).

### Token refresh (no new email code needed)

Captured alongside: once logged in, the app refreshes silently with the stored
refresh token — no OTP:

```
POST https://cognito-idp.eu-west-1.amazonaws.com/
X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth
{ "ClientId":"336gl87kpsv161e6kp6jdc6a3g",
  "AuthFlow":"REFRESH_TOKEN_AUTH",
  "AuthParameters":{"REFRESH_TOKEN":"<refresh token>"} }
--> 200 { "AuthenticationResult": { "AccessToken":..., "IdToken":... } }
```

So an integration needs the email OTP only at **initial setup** and again only
when the refresh token itself expires (~30 days, pool-configurable). Between
those it reconnects the WebSocket with a freshly-refreshed ID token.

## Everything needed to build the cloud path

1. **Setup:** user enters email → `InitiateAuth` (CUSTOM_AUTH) → user enters the
   emailed 6-digit code → `RespondToAuthChallenge` → store the refresh token.
2. **Connect:** refresh the ID token if needed (`REFRESH_TOKEN_AUTH`), then open
   `wss://nzp7wg4kbl.execute-api.eu-west-1.amazonaws.com/Prod/?token=<IdToken>`.
3. **Read:** send `{"action":"fetchDevices"}`; parse `devices`; also handle
   pushed `device`/`devices` messages for live updates.
4. **Control:** send `{"action":"updateDevice","id":"<serial>","state":{...}}`.
5. **Reconnect** with a refreshed token when the socket drops or the token
   nears expiry.

The transport is a plain JSON WebSocket — considerably simpler to implement than
the AWS IoT MQTT path first assumed.

## Notes / gotchas

- Cognito custom-auth sessions expire in ~3 min; scripted UI entry repeatedly
  lost the race. Completing the two Cognito calls directly (curl) is reliable
  because it removes UI latency, but it authenticates *your* client, not the
  app — so it does not by itself produce the app's post-login `GetId` traffic.
- No certificate pinning in the app (`network_security_config.xml` has no
  pin-set; a system-store CA decrypts everything). The `okhttp3.CertificatePinner`
  class is present but unused.
- The app is React Native + Hermes bytecode v96; config values live in the
  packed, sorted Hermes string table (so plain `grep` for full URLs/IDs mostly
  misses them). This is why runtime capture is required.
