# Reverse-engineering mobile-app APIs with mitmproxy + an Android emulator

When a mobile app talks to a private HTTP API and you want to know the real
shape of the traffic, the cheapest reliable setup is a rooted Android
emulator running the APK behind a mitmproxy that's trusted at the system
level. This document captures the exact procedure we used for the bsport
app so it can be re-applied to other projects.

It has three layers:

1. **Install the app in a controllable environment** (the emulator)
2. **Convince the OS to trust your proxy's TLS certificate** (system CA)
3. **Route the app's traffic through mitmproxy and decode it**

Layer 2 is the fragile one and the most common reason setups fail silently.

## Prerequisites

On macOS, one-time install:

```bash
brew install mitmproxy                 # proxy + cert authority
brew install --cask android-studio     # ships adb + emulator + SDK manager
```

In Android Studio, create an AVD (Device Manager → Create Virtual Device):

- Pick any phone profile (Pixel 6 Pro works fine)
- **System Image: Google APIs** — NOT *Google Play*. The Play image is
  production-locked, you cannot `adb root` it, which means you cannot
  install a system-level CA, which means HTTPS won't decrypt.
- Architecture: **arm64-v8a** on Apple Silicon, **x86_64** on Intel. The
  emulator picks the best host-virtualisation path automatically.

Verify:

```bash
ls ~/Library/Android/sdk/system-images/android-34/google_apis/
# should show the architecture folder
```

## Run the pipeline

The repository ships a script that orchestrates everything:

```bash
# scripts/mitm_android.sh <apk-or-split-apks...>
scripts/mitm_android.sh path/to/app.apk
```

What it does, in order:

1. Starts `mitmweb` on `:8080` (proxy) and `:8081` (UI), web auth disabled
   for local convenience, with `--save-stream-file /tmp/bsport_flows.mitm`
   so you can re-read the traffic later
2. Hashes the mitmproxy CA using OpenSSL's *old* subject hash (the format
   Android's cert loader expects) and stages a copy at `/tmp/<hash>.0`
3. Boots the AVD with `-writable-system -no-snapshot`. The writable-system
   flag mounts `/system` via overlayfs so changes persist; skipping
   snapshots avoids loading a stale pre-cert state
4. `adb root`, `adb disable-verity`, `adb remount` — three steps because
   production Android images ship with dm-verity blocking `/system` writes
5. Pushes the hashed cert into `/system/etc/security/cacerts/`, chmods it
6. Reboots once so the cert is active, then sets `settings global http_proxy`
   to `10.0.2.2:8080` (the emulator's alias for the host loopback)
7. Installs the APK(s) via `adb install-multiple`

Open `http://127.0.0.1:8081/` in your browser. Every request the emulator
makes should now be visible, with full URL/body/headers.

## Re-reading flows later

`mitmweb`'s in-memory feed resets when you restart it. The `-w` flag writes
a binary flow file that you can replay:

```python
from mitmproxy import io
from pathlib import Path
with Path("/tmp/bsport_flows.mitm").open("rb") as f:
    for flow in io.FlowReader(f).stream():
        print(flow.request.method, flow.request.pretty_url)
```

For a quick summary of decoded bodies, use `flow.request.get_text()` /
`flow.response.get_text()` — they auto-decompress brotli/gzip, which a
raw `.content` inspection won't.

## If decryption silently fails

Open the app, try to sign in, watch mitmweb. Three failure modes:

| Symptom | Cause | Fix |
|---|---|---|
| No bsport traffic at all | Emulator isn't using the proxy | `adb shell settings get global http_proxy` should return your host. Re-set if empty |
| `CONNECT host:443` lines with no decrypted body | Cert isn't trusted | Did you install to `/system/...`, not `/data/...`? After reboot, `adb shell ls /system/etc/security/cacerts/ | grep <hash>` should show your file |
| TLS handshake errors or connection resets | Certificate **pinning** is active — the app has the server's cert hash baked in and refuses any other | See next section |

## Bypassing certificate pinning

Modern fitness/banking/streaming apps often pin. In order of effort:

**1. Frida + a universal unpinner (low effort, high success rate)**

```bash
pip install frida-tools objection
adb shell pm list packages | grep -i <your.target>
objection --gadget <com.your.package> explore
# then inside the objection shell:
android sslpinning disable
```

Objection injects a Frida gadget at runtime and patches the common pinning
libraries (OkHttp, Conscrypt, native BoringSSL). Run `objection` alongside
the app — the app will keep running but its pinning checks will return
*trusted* regardless of the real chain.

**2. Patch the APK (no runtime agent, stable against updates)**

Use `apktool` to decompile, drop a `network_security_config.xml` that trusts
user CAs:

```xml
<network-security-config>
  <base-config cleartextTrafficPermitted="true">
    <trust-anchors>
      <certificates src="system"/>
      <certificates src="user"/>
    </trust-anchors>
  </base-config>
</network-security-config>
```

...then repack and re-sign with `uber-apk-signer`. Install the patched APK
(uninstall the original first). This sidesteps pinning entirely for apps
that honour `network_security_config`.

**3. React Native / Hermes specific**

If the bundle is Hermes bytecode (check with `file index.android.bundle` —
look for "Hermes JavaScript bytecode"), pinning is usually implemented in
one of `react-native-pinch` / `react-native-ssl-pinning` / custom Axios
interceptor. Frida usually handles these via the generic OkHttp hook; if
not, you can grep the decompiled bundle for `pinning` / `certificateHash`
to find where it's enforced.

## Adapting to other projects

The script is parameterized via env vars:

```bash
AVD=Pixel_7_API_34 EMU_PORT=5556 scripts/mitm_android.sh foo.apk
```

If the app uses split APKs (most modern ones do — Play Asset Delivery ships
architecture + density + language splits as separate files), pass them all:

```bash
scripts/mitm_android.sh base.apk split_config.arm64_v8a.apk \
                        split_config.xxhdpi.apk split_config.en.apk
```

Get the split APKs from an APK mirror site (apkpure, apkmirror) or by
extracting from an `.xapk` bundle (just a zip).

## When the emulator isn't enough

- **Hardware-only telemetry (HRM, NFC, etc.)**: run the app on a real
  device (same mitmproxy, install the CA manually via Settings → Security
  → Install CA cert; requires a device you've rooted or an app that honours
  user CAs via network_security_config)
- **Push notifications**: FCM traffic goes via Google, not the app's
  backend. You'll see the registration call to the backend but not the
  push payload. Intercept the app's FCM handler with Frida instead
- **WebSocket or gRPC**: mitmweb shows WebSocket frames inline; gRPC shows
  the raw protobuf — pair with `grpc-tools` / `grpcurl` to decode payloads
  if you have the `.proto`

## Cleanup

When you're done testing, stop the proxy and wipe the session artefacts:

```bash
pkill -f "mitmweb --listen-port 8080"
adb shell settings delete global http_proxy
rm -f /tmp/bsport_flows.mitm /tmp/*.0
```

The installed CA survives across AVD reboots (it's in `/system`), so the
next session starts from a ready state — or re-run the script to be sure.
