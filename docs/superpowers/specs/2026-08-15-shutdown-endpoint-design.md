# Token-guarded shutdown endpoint — design

**Date:** 2026-08-15
**Status:** approved, ready for implementation

## Goal

Let the robot be powered down cleanly over HTTP, so the Pi can be shut down
without SSH and without the Pimoroni OnOff SHIM.

## Why

The OnOff SHIM provides the soft power switch. It has not arrived yet, and
pulling USB power without a clean shutdown risks SD card corruption. `POST
/stop` sounds like it would help but does not — it runs a mechanical
interrogation to find the stop cam and has nothing to do with system power.

This endpoint stays useful after the SHIM is wired (shutting down from a phone
without walking to the robot), but it is deliberately small: the SHIM is the
real answer, and this should not grow into a general remote-administration
surface.

## Security context

The API has **no authentication**, binds `0.0.0.0:8080`, and advertises itself
over mDNS. Anything on the LAN can discover the robot and drive it. That is an
accepted trade-off for the existing endpoints, where the worst case is someone
waving the robot's arms.

Remote poweroff is a different proposition, so this endpoint is opt-in and
separately authenticated rather than inheriting the open surface.

## Deliverables

| File | Change |
|---|---|
| `src/verbot/config.py` | add `shutdown_token: str \| None = None` |
| `src/verbot/hardware/protocols.py` | add `SystemPower` protocol |
| `src/verbot/hardware/system_power.py` | new — `SubprocessPower` adapter |
| `src/verbot/hardware/fakes.py` | add `FakePower` |
| `src/verbot/main_support.py` | add `build_power(settings)` |
| `src/verbot/controller.py` | add `Controller.halt()` |
| `src/verbot/api.py` | widen `create_app`, add the route |
| `src/verbot/__main__.py` | pass `settings` and `power` to `create_app` |
| `tests/test_api.py` | update fixture, add endpoint tests |
| `tests/test_controller.py` | add `halt()` test |
| `tests/test_main.py` | add `build_power` test |
| `docs/deployment.md` | sudoers rule, token generation, caveats |
| `README.md` | one line in Features |

## The off switch

`Settings` gains:

```python
shutdown_token: str | None = None
```

When it is `None`, **the route is never registered**. Not registered-then-403:
genuinely absent, so it is also missing from `/docs` and `/openapi.json` and
the capability is not advertised to anyone scanning. The existing API surface
is byte-identical unless the operator opts in.

## SystemPower protocol

Follows the established `MotorDriver` / `SpeechEngine` / `Keypad` pattern, so
the suite keeps running off-Pi with no hardware and no root.

```python
# protocols.py
class SystemPower(Protocol):
    async def shutdown(self) -> None:
        """Power the machine off. Returns once the request is issued."""
```

`hardware/system_power.py` holds `SubprocessPower`, which shells out via
`asyncio.create_subprocess_exec`, mirroring how `EspeakEngine` already invokes
`espeak-ng`. `fakes.py` holds `FakePower`, which records `shutdown_called`.

`main_support.build_power(settings)` mirrors `build_hardware` and
`build_keypad`: **it returns `FakePower` whenever `use_real_hardware` is
false.** This is a safety property, not a convenience one — without it, running
the dev server on a laptop with a token configured would power off the laptop.

## The endpoint

```
POST /system/shutdown
  X-Verbot-Token: <token>

  202 { "status": "shutting down" }
  401 missing or incorrect token
  404 endpoint not configured
```

- The token is compared with `secrets.compare_digest`, so comparison is
  constant-time. A missing header short-circuits to a failure rather than being
  passed to `compare_digest`, which rejects `None`.
- On success the handler halts the motor, then schedules the poweroff as a
  FastAPI `BackgroundTask` so the 202 reaches the client before the machine
  dies.
- Returns 202 rather than 200: the shutdown has been accepted, not completed.

### Wiring

`create_app` widens to take the two new dependencies explicitly, rather than
reaching for globals:

```python
def create_app(
    controller: Controller,
    speech: SpeechEngine,
    settings: Settings,
    power: SystemPower,
) -> FastAPI:
```

Both new parameters are required. There are only two call sites —
`src/verbot/__main__.py:49` and the `client_rig` fixture in
`tests/test_api.py:16` — so widening is cheap, and a default would let a caller
silently get an app with no shutdown route when they meant to configure one.

`build_app` calls `build_power(settings)` alongside the existing
`build_hardware` and `build_keypad`, and passes both `settings` and `power`
through.

### Controller.halt()

A new method — cancel the pending timeout, then `set_speed_percent(0)`.

Strictly this is redundant: `poweroff` causes systemd to SIGTERM the unit,
which runs the FastAPI lifespan teardown, which already calls
`controller.close()` and stops the motor. Two reasons to do it explicitly
anyway:

1. That teardown is a few hundred milliseconds away, and the robot is driving
   for all of them.
2. If the sudoers rule is missing or wrong, `poweroff` fails silently and the
   motor would otherwise keep running indefinitely.

"Shutdown stops the robot" is the least surprising behaviour.

This is **not** `Action.STOP`. That runs a mechanical interrogation to find the
stop cam — the opposite of what is wanted when powering down.

## Deployment

The unit runs as an unprivileged `$USER`, so `poweroff` needs one narrow grant:

```
# /etc/sudoers.d/verbot-poweroff   (mode 0440, validate with visudo -c)
<user> ALL=(root) NOPASSWD: /usr/sbin/poweroff
```

Scoped to exactly that binary — not `NOPASSWD: ALL`. Confirm the real path with
`command -v poweroff` first: `/usr/sbin` on Trixie, `/sbin` on older images. A
wrong path fails closed.

Polkit is not an alternative: a systemd service has no seat or login session,
so the usual "a local user may power off without sudo" path does not apply.

`docs/deployment.md` documents the sudoers rule, token generation
(`openssl rand -hex 32`), and placing the token in `.env` beside
`VERBOT_USE_REAL_HARDWARE`. It also records that the token travels as a
plaintext header over unencrypted HTTP — acceptable on a home LAN, worth
knowing before exposing this more widely.

## Testing

TDD throughout, following the existing `client_rig` fixture pattern in
`tests/test_api.py`, plus a second fixture that builds an app with a token set.

| Test | Asserts |
|---|---|
| token unset | 404, and the path is absent from `/openapi.json` |
| token set, no header | 401, `shutdown_called` False |
| token set, wrong token | 401, `shutdown_called` False |
| token set, correct token | 202, `shutdown_called` True, motor speed 0 |
| `build_power` off-Pi | returns `FakePower` when `use_real_hardware` is false |
| `Controller.halt()` | sets speed 0 and cancels the pending timeout |

The negative cases carry more weight than the positive one: "wrong token still
powers off the robot" is the failure that costs something, and it is exactly
what a happy-path-only suite ships.

No new dependencies — `secrets` is stdlib and `BackgroundTasks` is already part
of FastAPI. The suite must keep running off-Pi with no hardware and no root.

## Out of scope

- Reboot. Shutdown is the need; a second route can be added later if wanted.
- Any other system or administration endpoints.
- Authentication for the existing endpoints. That is a separate decision, and
  bundling it here would hide it.
- HTTPS or transport security.
