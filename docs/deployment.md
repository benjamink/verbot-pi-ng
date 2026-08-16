# Deploying to the Pi Zero 2 W

## 1. Base image

Flash **64-bit Raspberry Pi OS (Trixie)**. The 64-bit build matters: it gets
prebuilt aarch64 wheels for `pydantic-core`, avoiding a from-source Rust build
that a Zero would struggle to finish.

## 2. Headless first boot

Raspberry Pi OS ships in two variants that provision from **different files**.
Check `bootfs` before writing anything — the wrong file is silently ignored,
and you get the interactive user-creation wizard on a robot with no keyboard:

| On `bootfs` | Variant | Provision with |
|---|---|---|
| `user-data`, `meta-data`, `network-config` | cloud-init | `user-data` + `network-config` |
| neither, and `cmdline.txt` contains `init=/usr/lib/raspberrypi-sys-mods/firstboot` | standard | `custom.toml` |

We use the **cloud-init** variant. Replace the two stock templates, which ship
entirely commented out and therefore do nothing.

`user-data`:

```yaml
#cloud-config
hostname: verbot
manage_etc_hosts: true

bootcmd:
  - [ systemctl, disable, userconfig.service ]
  - [ systemctl, enable, getty@tty1.service ]

users:
  - name: bkrein
    shell: /bin/bash
    groups: [adm, sudo, users, dialout, audio, video, plugdev, netdev, gpio, i2c]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: false
    passwd: "$6$..."          # openssl passwd -6 'yourpassword'
    ssh_authorized_keys:
      - "ssh-ed25519 AAAA... you@workstation"

ssh_pwauth: false
timezone: America/New_York

runcmd:
  - [ systemctl, enable, --now, ssh ]
```

`network-config`:

```yaml
network:
  version: 2
  wifis:
    wlan0:
      dhcp4: true
      optional: true
      access-points:
        "YourNetwork":
          password: "your-wifi-passphrase"
      regulatory-domain: US
```

Then in `meta-data`, set **`instance-id`** — with a hyphen — to a new value:

```yaml
dsmode: local
instance-id: verbot-2026-08-16-02
```

This is the single most important line, and the stock template gets it wrong.
It ships `instance_id` with an **underscore**, which is not a cloud-init key at
all. cloud-init reads `instance-id` (see `iid_key` in `DataSourceNoCloud.py`),
so with the underscore spelling the id silently stays at its NoCloud default of
`nocloud` forever. Every `once-per-instance` module — `users_groups`,
`set_passwords`, `ssh` — then runs on the very first boot and is skipped on
every boot after, no matter how many times you edit `user-data`.

The failure is deeply misleading, because `update_hostname` runs
`once-per-always`. Your new hostname appears, which looks like the config was
applied, while no user was ever created. If `/etc/hostname` is right but
`/etc/passwd` has only pi-gen's `pi` placeholder, this is why.

The `bootcmd` block retires the first-boot wizard. `userconfig.service` is
enabled and prompts for a username on **every** boot until something calls
`cancel-rename`, which nothing does on a declaratively provisioned image.
`bootcmd` runs in the init stage, well before `multi-user.target`, so it cannot
race the wizard. Re-enabling `getty@tty1` is not optional: the image ships it
disabled because the wizard owns the console, so disabling the wizard alone
leaves no console login at all.

Use canonical timezone names. Debian 13 moved legacy names like `US/Eastern`
into the separate `tzdata-legacy` package, which a Lite image does not carry —
`cc_timezone` raises `IOError` and the module fails. `America/New_York` works.
Note that `timedatectl` on an Arch workstation happily reports the legacy name,
so copying it across is an easy mistake.

**List `gpio` and `i2c` here, not only in step 6.** `cc_users_groups` re-applies
this list verbatim every time `instance-id` changes, so any group added later
with `usermod` is silently removed on the next re-provision. That drops write
access to `/sys/class/pwm` (`root:gpio`, mode 770) and the service dies at
startup with `PermissionError: … '/sys/class/pwm/pwmchip0/export'`, which looks
nothing like a user-management problem.

Both groups exist on a stock Raspberry Pi OS image. Do not add speculative
groups — a name that does not exist makes `useradd` fail and takes the whole
user creation down with it, landing you back at the wizard.

`optional: true` keeps a missing access point from blocking boot. The robot is
built to run its panel buttons with no network (bring-up checklist step 7), so a
hung boot is the worse failure.

### If it comes up at the wizard anyway

Reseat the card and read `bootfs`. The state of the provisioning file says which
half of the pipeline broke:

- **`custom.toml` still present** — you are on the cloud-init variant and it was
  never read. The standard variant *deletes* the file after applying it.
- **`/etc/hostname` correct but no user in `/etc/passwd`** — `instance-id` was
  unchanged, so the per-instance modules were skipped. Confirm with
  `ls /var/lib/cloud/instances/`: a directory named `nocloud` rather than your
  id means the key never took effect.
- **A module failed** — `grep -E "WARNING|Traceback" /var/log/cloud-init.log` on
  `rootfs`. The log is `root:0640`, so it needs sudo to read.
- **The Pi has no network but is otherwise configured** — the Zero 2 W radio is
  2.4 GHz only. Confirm the SSID exists on 2.4 GHz with WPA2.

Timestamps in that log are unreliable. The Zero 2 W has no RTC, so `fake-hwclock`
restores the image build date and every entry looks months old. Judge ordering by
sequence, never by clock time.

The pre-Bookworm `ssh` and `wpa_supplicant.conf` files work on neither variant.
Trixie uses NetworkManager, and `wpa_supplicant.conf` in the boot partition is
ignored outright.

## Fast path: the install script

Steps 3–8 are automated. Once you can SSH in:

```bash
curl -LsSf https://raw.githubusercontent.com/benjamink/verbot-pi-ng/main/scripts/install.sh | bash
```

It is idempotent — re-run it after a push to update the checkout and
dependencies. It refuses to run on anything that is not a 64-bit Raspberry Pi,
backs up `config.txt` to `config.txt.verbot-backup` before appending, and marks
its additions with `# >>> verbot-pi-ng >>>` so a second run is a no-op.

The service is enabled, so it starts on boot and the panel buttons are live.
**Put the robot on a stand with its wheels off the ground before rebooting.**

The script enables the service but does not start it before the reboot: the PWM
overlay is not loaded until then, so starting early would fail on a missing
`pwmchip0` and trip `Restart=on-failure`.

The rest of this document explains what the script does, and is what to follow
if you would rather work through it by hand.

## 3. Firmware config

Append the contents of [`../config/config.txt.example`](../config/config.txt.example)
to `/boot/firmware/config.txt` and reboot. Then verify:

`dtparam=i2c_arm=on` brings up the bus but does **not** create the `/dev/i2c-*`
character devices that `smbus2` opens. Those need the `i2c-dev` module, which
nothing loads by default — `raspi-config`'s "enable I2C" does both halves, and
the dtparam is only the first:

```bash
sudo modprobe i2c-dev
echo i2c-dev | sudo tee -a /etc/modules   # survive a reboot
```

Then verify:

```bash
ls /sys/class/pwm/          # expect pwmchip0
cat /sys/class/pwm/pwmchip0/npwm   # expect 2 — pwm-2chan gives both channels
aplay -l                    # expect the MAX98357A card
ls /dev/i2c-*               # expect /dev/i2c-1
i2cdetect -y 1              # expect a device at 0x20
```

If `pwmchip0` is absent or numbered differently, set `VERBOT_PWM_CHIP`
accordingly — kernel PWM chip numbering has shifted between OS releases.

If `/sys/bus/i2c/devices/` lists `i2c-1` but `/dev/i2c-1` is missing, the bus is
enabled and only `i2c-dev` is absent. A missing MCP23017 is not the cause: the
bus node exists whether or not anything is wired to it, and shows up as an empty
`i2cdetect` grid rather than a missing device file.

**Append the block once.** If you have already appended it by hand, the install
script will detect the existing `dtoverlay=pwm-2chan` and skip rather than add a
second copy — duplicated `dtoverlay` lines load the overlays twice.

If `npwm` reads 1, the single-channel `pwm` overlay is still in place. The
DRV8833 needs `pwm-2chan`: it has no PHASE/ENABLE mode, so both IN1 and IN2
need a PWM channel.

## 4. System packages

```bash
sudo apt update
sudo apt install -y espeak-ng i2c-tools
sudo apt install -y build-essential python3-dev swig liblgpio-dev
espeak-ng "hello"      # confirm audio reaches the speaker
```

The second line is the build toolchain for `lgpio`. It publishes aarch64 wheels
only up to **cp312**, and this image runs CPython 3.13, so there is no wheel to
match and `uv` compiles the sdist here. `uv.lock` records only the sdist for the
same reason, so this is not something a lockfile refresh avoids. Without these
packages the sync fails at `error: command 'swig' failed`.

## 5. Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/benjamink/verbot-pi-ng.git ~/verbot-pi-ng
cd ~/verbot-pi-ng
uv sync --extra pi --frozen
```

`--frozen` installs exactly the committed `uv.lock`.

## 6. Permissions

```bash
sudo usermod -aG gpio,i2c,audio "$USER"
```

Log out and back in. If writes to `/sys/class/pwm` still fail, check for a udev
rule granting the `gpio` group access; add `/etc/udev/rules.d/99-pwm.rules` if
your image lacks one.

## 7. First run

```bash
VERBOT_USE_REAL_HARDWARE=true uv run verbot
```

Without that variable the server runs on fakes and the robot will not move —
useful for testing the API on any machine, confusing if you forget it on the Pi.
Look for the absence of the `running on fake hardware` warning in the log.

## 8. Service

```bash
sudo cp config/verbot.service /etc/systemd/system/verbot@.service
sudo systemctl daemon-reload
sudo systemctl enable --now verbot@$USER
journalctl -u verbot@$USER -f
```

Put `VERBOT_USE_REAL_HARDWARE=true` in a `.env` file in the working directory,
or add `Environment=` lines to the unit.

## Bring-up checklist

There is a control page at **`http://verbot.local:8080/`** covering every step
below except the panel buttons: action buttons, a large stop, live status, a
speak box, live speed sliders, and a log of each request and response. It calls
the same endpoints as the `curl` commands here, so either works.

Work through these in order, with the robot **on a stand with its wheels off
the ground** until step 5 passes. Keep the schematic open while you do —
[`hardware/verbot-schematic.pdf`](../hardware/verbot-schematic.pdf)
([SVG](../hardware/verbot-schematic.svg)).

| # | Check | Command / action | Expected |
|---|-------|------------------|----------|
| 1 | Server healthy | `curl -s localhost:8080/healthz` | `{"status":"ok"}` |
| 2 | Switches read | Turn the drum **by hand**, watch `journalctl -f` | each switch logs a close then an open |
| 3 | Interrogation → action | `curl -X POST localhost:8080/actions/talk` | motor runs, then reverses when the talk switch closes |
| 4 | Stop | `curl -X POST localhost:8080/stop` | motor stops at the stop position |
| 5 | Limit switch | `curl -X POST localhost:8080/actions/pick_up` | arms rise and **stop at the top by themselves** |
| 6 | Speech | `curl -X POST localhost:8080/say -H 'content-type: application/json' -d '{"text":"I am Verbot"}'` | audible |
| 7 | Panel buttons | `sudo ip link set wlan0 down`, press each button | robot responds with no network |
| 8 | Watchdog | disconnect one switch, request that action | motor stops after the timeout, status shows `fault` |

If the motor never turns at all, check `EEP`/nSLEEP first: the DRV8833
tri-states its outputs until that pin is driven high, so a mis-wired sleep pin
looks exactly like a dead motor.

If the motor turns the wrong way, swap `OUT1` and `OUT2` rather than the sign
conventions in the code — interrogation must be the positive direction.

If step 3 never reverses, the switch wiring or polarity is wrong — check
`switch_event()` in `src/verbot/hardware/lgpio_switches.py` and confirm the pins
read low when closed.

If step 7 triggers the wrong action, reorder `BUTTON_ORDER` in
`src/verbot/hardware/mcp23017.py` to match the physical panel layout. The order
committed there is a guess.

## Values to measure and record

The defaults are inherited guesses. Replace them with measurements:

| Setting | Default | How to determine |
|---------|---------|------------------|
| `VERBOT_INTERROGATION_SPEED` | 50 | Lowest speed that still turns the drum reliably. Slower gives more reliable switch detection. |
| `VERBOT_INTERROGATION_TIMEOUT_S` | 10.0 | Time a full drum revolution takes, roughly doubled. |
| `VERBOT_ACTION_SPEED` | -100 | Reduce if the mechanism sounds strained. Also cap it if the DRV8833's `VCC` is fed from 5V: the motor is a 3V part, so ±60 is roughly its rated voltage. |
| `VERBOT_MOTOR_SLEEP_PIN` | 6 | Set to `null` if the carrier's `J1` jumper is bridged — nSLEEP is then tied high in hardware and BCM 6 stays free. |

Record what you land on here once measured.
