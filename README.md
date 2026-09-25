# Dolby IMS3000 for Home Assistant

[![Validate](https://github.com/Videobarista/dolby-ims3000-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/Videobarista/dolby-ims3000-ha/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Videobarista&repository=dolby-ims3000-ha&category=integration)
[![GitHub release](https://img.shields.io/github/v/release/Videobarista/dolby-ims3000-ha)](https://github.com/Videobarista/dolby-ims3000-ha/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Home Assistant integration for Dolby (formerly Doremi) digital cinema servers, speaking the
native KLV control protocol on TCP **11730**.

Verified in day-to-day use against the **IMS3000** and the **DCP-2000**. It should also work
on the IMS2000, IMS1000, ShowVault and DCP-2K4, which share the same intra-theatre message
set — if you run one of those, an issue with your results is welcome.

---

## Entities

| Entity | Type | What it does |
|---|---|---|
| Media player | `media_player` | Playback state, current title, position/duration, play, pause, show selection |
| Scheduler | `switch` | Enables or disables the server's automatic show scheduler |
| Armed show | `select` | Picks which show playlist play actions will target |
| Playback state | `sensor` | Stopped / playing / paused |
| Current title | `sensor` | Content title of the composition on screen |
| Playlist position, duration, time remaining | `sensor` | Progress through the running show |
| Show playlists, compositions, keys | `sensor` | Library counts, with IDs and titles as attributes |
| Next scheduled show | `sensor` | Upcoming schedule entry and its annotation |
| Response time, last seen | `sensor` | Round-trip time of the last poll, and when the server was last reachable |
| Serial number, software version, API version, time zone | `sensor` | Diagnostic, disabled by default |
| Online | `binary_sensor` | Connectivity, stays available when the server is not |
| Show running, show loaded | `binary_sensor` | Quick automation triggers |
| Content encrypted | `binary_sensor` | Whether the running composition is AES-encrypted |

## Services

| Service | Purpose |
|---|---|
| `dolby_ims3000.play_spl` | Schedules a show playlist to start after a short delay |
| `dolby_ims3000.validate_cpl` | Asks whether a composition is playable now, including key status |
| `dolby_ims3000.ingest_job` | Submits an ingest job |
| `dolby_ims3000.raw_command` | Sends any supported protocol command and returns the decoded reply |

`raw_command` exists so you can probe your own hardware from Developer Tools without editing
Python. Outside Home Assistant, `tools/probe.py` does the same from a terminal — read-only, no
dependencies beyond Python 3. Start with `python3 tools/probe.py <host> --list-commands`.

---

## Power-down handling

Cinema servers are switched off outside show hours, including while Home Assistant itself is
restarting — so an unreachable server is treated as a normal state, not a failure, from the
very first poll onward. The media player reports `off`, the connectivity binary sensor goes
off, and everything else that depends on the server clears rather than freezing on stale data.
Setup completes normally even if the server happens to be off at the time; there is no retry
loop and no traceback in the log, only a single INFO line when the server drops out and one
when it returns. The `Last seen` sensor stays readable throughout, so history remains useful
and you can automate on the server being off.

---

## Installation

### HACS

Click the badge above the title to open this repository directly in HACS, or add it manually:
three-dot menu → **Custom repositories** → paste this repository's URL → category
**Integration** → install → restart Home Assistant.

### Manual

Copy `custom_components/dolby_ims3000` into your Home Assistant `config/custom_components/`
directory and restart.

### Configuration

Settings → Devices & Services → **Add Integration** → *Dolby IMS3000*.

You will be asked for the host, the port (default 11730) and whether to allow playback control.
Everything else lives in the integration's **Configure** dialog afterwards.

The KLV port requires no authentication — access is controlled by network reachability. Keep
your projection network segmented.

---

## Important limitations

These are properties of the protocol, not omissions in this integration.

**There is no stop command.** The message set exposes play and pause only. The media player
therefore advertises play and pause, and no stop.

**There is no volume control.** Audio level lives in the sound processor — a CP750, CP850 or
CP950 — which is a separate device with a separate API.

**There is no "load and play".** The server plays what is already loaded and paused. Nothing
else. When nothing is loaded, this integration falls back to queueing the armed show with the
scheduler a few seconds out, which is how the scheduler is designed to be driven. If you want
deterministic starts, let shows load via the server's own schedule and use play to release them.

**Show playlists have no names of their own.** `GetSPLList` returns bare UUIDs with no
accompanying metadata — there is no command to look one up by id either. The only place a show
name appears anywhere in the protocol is a scheduler entry's annotation text, so the show
picker learns a name for an SPL once it has been seen as the current or next scheduled show,
and falls back to the UUID's first octets (`SPL 851cc838`) until then. A show played by manually
arming and pressing play, never scheduled, keeps the UUID label. Compositions do carry titles,
so the *current title* sensor always shows something readable regardless.

**Playlist counters are in edit units.** The server reports playlist position and duration in
frames, not seconds, and separately reports the edit rate of the element on screen. The
integration converts using that rate by default. If your server behaves differently, the
*Playlist position unit* option can force seconds or edit units.

---

## Security

The KLV protocol has no authentication and no encryption: anyone who can reach TCP 11730 on
the server can control it. Keep cinema servers on a separate VLAN and never expose them to
the internet. Playback control is off by default. See [SECURITY.md](SECURITY.md) for the
full picture and how to report a vulnerability.

---

## Safety

This talks to equipment that runs live performances.

- Control is a per-server option. Turn it off and the integration becomes read-only; play, pause
  and scheduler changes are refused with a clear error.
- Polling is deliberately split: playback state is read often, the content catalogue rarely.
  Raise *Catalogue refresh* to keep traffic off the projection network.
- One TCP connection is held open and every command is serialised behind a lock, because the
  protocol has no way to correlate overlapping requests.

Do not point this at a production screen during a show until you have verified it on a spare unit.

---

## Protocol notes

The wire format is a SMPTE fixed-length KLV pack:

```
+----------------+---------+---------+---------------+-------------+
| 13-byte header | 3b key  | BER len | 4b request id | payload ... |
+----------------+---------+---------+---------------+-------------+
                           \______ BER covers id + payload ________/
```

The header `060E2B340205010A0E10010101` identifies a Doremi Labs DCP-2000 intra-theatre message
pack. Response keys are the request key with the middle byte incremented. Most responses end in
a single-byte return code, zero meaning success.

Protocol knowledge is derived from [`ronhanson/python-dcitools`](https://github.com/ronhanson/python-dcitools)
(MIT). Three defects in that reference are corrected here and marked `FIXED:` in `klv.py`:

1. Fixed-width text fields were padded on the **left** with NUL bytes rather than the right.
2. `AddSchedule2` and `GetScheduleInfo2` passed a `size=` argument to integer encoders that only
   accept `bit=`, so those commands could never have executed as written.
3. `GetScheduleInfo2` wired its response fields to *encoder* functions, which cannot decode.

Device information is also available over SOAP on port 80 at `/dc/dcp/ws/v1/`, with WSDL files
downloadable from the server's own web interface. That path is not used here yet.

---

## Branding

Home Assistant and HACS can show an icon and logo for this integration. No manufacturer
artwork ships here: the Dolby and Doremi marks are trademarks of their owners and this
project is not affiliated with them.

`custom_components/dolby_ims3000/brand/icon.png` (and `icon@2x.png`) hold a blank placeholder
— a plain outline square, nothing more — so HACS's brand check passes. Replace them with real
artwork if you have the rights to some, or leave them as-is; the integration works either way.

`Brand/` is a separate, empty spot with the required sizes documented for anyone who wants to
contribute proper artwork for wider distribution.

---

## Credits

The KLV command and response tables this implementation is built on come from
[`ronhanson/python-dcitools`](https://github.com/ronhanson/python-dcitools), MIT licensed.

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 VideoBarista.

---

This integration is an independent, community-built project. It is **not affiliated with,
endorsed by, or supported by Dolby Laboratories, Inc. or Doremi Labs** in any way. "Dolby",
"Doremi", "IMS3000" and related names are trademarks of their respective owners and are used
here only to describe the hardware this software communicates with. Use it at your own risk:
the authors accept no responsibility for disrupted screenings, damaged equipment, or any other
loss arising from its use.
