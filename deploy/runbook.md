# Wheel of Misfortune — runbook

Single-host, LAN-only deployment on **macOS or Linux**. The host runs the server;
phones/tablets use it over Bonjour/mDNS and add it to the Home Screen as a PWA.
**Never port-forward this.** Sections 1–8 below are macOS (launchd); §9 maps every
step to its Linux (systemd) equivalent. `deploy/install.sh` auto-detects the OS.

## 1. First install

```bash
cd wheel-of-misfortune
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp family.example.yaml family.yaml      # then edit: players, chores, effort, ick
./deploy/install.sh                      # copies the LaunchAgent, loads it
```

`install.sh` substitutes this repo's absolute path into the plist, creates
`logs/`, copies it to `~/Library/LaunchAgents/com.storey.misfortune.plist`, and
loads it with `RunAtLoad` + `KeepAlive`. The Mac is **not** forced awake — the
server runs whenever the Mac is awake and sleeps with it (see "Sleep" below).

### Friendly address (Bonjour)
System Settings → General → Sharing → **Local hostname** → `misfortune`.
The app is then at **http://misfortune.local:8000** for every Apple device on
the Wi-Fi. (Until you set it, `install.sh` prints the current `<hostname>.local`.)

### Sleep — the server sleeps with the Mac (by design)
It's a chore wheel, not a 24/7 service, so we don't `caffeinate` a laptop into
never sleeping. It's reachable whenever the Mac is awake. Opt-ins if you want more:

- **A wake window** (up during the day, asleep overnight):
  ```bash
  sudo pmset repeat wakeorpoweron MTWRFSU 07:00:00 sleep MTWRFSU 23:30:00
  ```
- **Truly always-on:** run it on a low-power box (Raspberry Pi / mini / NAS) — see §9.
- **Never sleep** (old behaviour; not recommended on a laptop): `sudo pmset -a sleep 0 disksleep 0`.

### On each phone
Open the URL in **Safari** → Share → **Add to Home Screen**. Launches
full-screen; the service worker caches the shell so it opens instantly even if
the Wi-Fi blips. (Live data still needs the server — `/api` is never cached.)

## 2. Daily operation

It just runs. To check it's alive:

```bash
launchctl list | grep misfortune                 # PID + last exit code
curl -s http://localhost:8000/api/state | head    # should return JSON
tail -f logs/misfortune.err.log                   # uvicorn output
```

State lives in `state.json` (atomic writes). Week rollover is automatic on the
first request of a new ISO week; surplus effort carries as **banked**.

## 3. Updating the app

```bash
git pull
source .venv/bin/activate && pip install -r requirements.txt   # if deps changed
launchctl kickstart -k "gui/$(id -u)/com.storey.misfortune"     # restart
```

If you only changed front-end files, just reload the PWA — but the service
worker caches the shell, so bump `VERSION` in `web/sw.js` to force clients to
pick up `app.js`/`styles.css` changes.

Re-run `./deploy/install.sh` only if the plist itself or the repo path changed.

## 4. Editing chores

Edit `family.yaml`, then restart (step 3). Seed validation is friendly: a bad
kind/effort/ick/freq fails with the **line number**. History, ledger, banked
effort and the φ-cursor are preserved across a reseed.

## 5. Backups

`state.json` is the only thing you can't regenerate. The `backups/` dir is a
fine spot for a periodic copy:

```bash
cp state.json "backups/state-$(date +%F).json"
```

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| Phone can't load it | Same Wi-Fi? `ping misfortune.local`. Mac firewall allowing uvicorn? |
| `launchctl list` shows non-zero exit | `logs/misfortune.err.log` — usually a bad `family.yaml` (line number is in the log). |
| Server not restarting | `KeepAlive` is on; confirm the plist loaded: `launchctl print gui/$(id -u)/com.storey.misfortune`. |
| Unreachable when Mac's asleep | Expected — it sleeps with the Mac. Want it up longer? `pmset repeat` (a wake window) or move it to a Pi (§9). |
| Stale UI after update | Bump `VERSION` in `web/sw.js`, reload twice. |

## 7. Uninstall

```bash
./deploy/install.sh uninstall     # unload + remove the LaunchAgent
```

`state.json`, `family.yaml` and `logs/` are left untouched.

## 8. Regenerating icons

The PWA icons are *drawn* by a stdlib-only script (no image library):

```bash
python deploy/make_icons.py       # rewrites web/icons/icon-{180,192,512}.png
```

## 9. Running on Linux (systemd)

The app itself is OS-neutral — only the keep-it-running layer differs. Install is
the same, and `./deploy/install.sh` detects Linux and uses a **systemd `--user`
service** (`deploy/wheel-of-misfortune.service`) instead of launchd:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp family.example.yaml family.yaml      # then edit
./deploy/install.sh                      # installs the systemd --user unit, starts it
```

`install.sh` enables the unit (`Restart=always` ≈ KeepAlive) and runs
`sudo loginctl enable-linger "$USER"` so it **starts at boot without a login**
(≈ launchd `RunAtLoad`). Mac → Linux command map:

| macOS (launchd) | Linux (systemd `--user`) |
| --- | --- |
| `launchctl list \| grep misfortune` | `systemctl --user status wheel-of-misfortune` |
| `tail -f logs/misfortune.err.log` | `journalctl --user -u wheel-of-misfortune -f` |
| `launchctl kickstart -k …` (restart) | `systemctl --user restart wheel-of-misfortune` |
| `./deploy/install.sh uninstall` | same (removes the unit, disables it) |
| `pmset -a sleep 0` (anti-sleep) | servers don't suspend; a **laptop** → `sudo systemctl mask sleep.target suspend.target` |

**`.local` is optional — you don't need to install anything.** The app already works at
`http://<server-ip>:8000`; just bookmark the IP. macOS/iOS clients get `.local` resolution
for free (built-in mDNS), so they can use `misfortune.local` regardless. The *only* thing
Avahi adds is letting the **Linux host itself** answer to that friendly name too — a pure
convenience. If (and only if) you want it, it's an opt-in extra daemon:

```bash
sudo apt install avahi-daemon avahi-utils     # Debian/Ubuntu (dnf/pacman on others)
hostnamectl set-hostname misfortune           # → http://misfortune.local:8000
```

No appetite for an extra service on the box for a chore wheel? Skip it — the IP is fine.

Headless/SSH note: `systemctl --user` needs a user bus. If `install.sh` can't reach
it, run `sudo loginctl enable-linger "$USER"` first, then re-run the installer (or
`export XDG_RUNTIME_DIR=/run/user/$(id -u)` for the current shell).
