# Dolby IMS3000 for Home Assistant

Home Assistant integration for Dolby (formerly Doremi) digital cinema servers, speaking the
native KLV control protocol on TCP **11730**.

Built for the **IMS3000**. It should also work on the IMS2000, IMS1000, ShowVault, DCP-2000
and DCP-2K4, which share the same intra-theatre message set.

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
Python. It is the fastest way to help verify this integration.

---

## Installation

### HACS

HACS → three-dot menu → **Custom repositories** → paste this repository's URL → category
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

**Show playlists have no names.** `GetSPLList` returns bare UUIDs with no accompanying metadata,
so the show picker labels entries by their first octets (`SPL 851cc838`). Compositions do carry
titles, so the *current title* sensor shows something readable.

**Playlist position units are unconfirmed.** The protocol documentation does not state whether
playlist position and duration are seconds or edit units. The default assumes seconds; if your
durations look wrong by roughly the frame rate, switch the *Playlist position unit* option.

---

## Verifying against your server

A standalone probe ships in `tools/`. It needs nothing but Python 3 — no Home Assistant, no
dependencies — and issues **read-only commands only**.

```bash
python3 tools/probe.py 10.0.0.50            # sweep the read-only command set
python3 tools/probe.py 10.0.0.50 --full     # also dump per-composition and per-key detail
python3 tools/probe.py 10.0.0.50 --command GetSPLList
python3 tools/probe.py --list-commands      # every command this build knows
```

The offline test suite exercises framing, encoding, decoding and the client's socket handling
against a loopback server:

```bash
python3 tools/selftest.py
```

If you have hardware, the most useful things to report are: anything printed as *decode failure*
or *unknown key*, whether position and duration look like seconds or frames, and the product
name and software version your unit reports.

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

## Credits

- [`ronhanson/python-dcitools`](https://github.com/ronhanson/python-dcitools) — the KLV command
  and response tables this implementation is built on.
- [`jamiegau/cinema-nmap-scripts`](https://github.com/jamiegau/cinema-nmap-scripts) — SOAP
  endpoint discovery and device fingerprinting.

## License

MIT — see [LICENSE](LICENSE). Copyright © 2026 VideoBarista.

---

This integration is an independent, community-built project. It is **not affiliated with,
endorsed by, or supported by Dolby Laboratories, Inc. or Doremi Labs** in any way. "Dolby",
"Doremi", "IMS3000" and related names are trademarks of their respective owners and are used
here only to describe the hardware this software communicates with. Use it at your own risk:
the authors accept no responsibility for disrupted screenings, damaged equipment, or any other
loss arising from its use.
