# Security Policy

## Supported versions

Only the latest released version is supported. Fixes are shipped in a new release rather
than backported.

| Version | Supported |
|---|---|
| latest release | yes |
| older releases | no |

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/Videobarista/dolby-ims3000-ha/security/advisories/new)
rather than in a public issue.

Expect an acknowledgement within a few days. This is a hobby project maintained in spare
time, so please allow reasonable time for a fix before public disclosure.

## Scope

This integration speaks the Doremi/Dolby KLV control protocol over a plain TCP socket on
your own network. Relevant security properties:

- **No authentication, no encryption.** The protocol offers neither. Anyone who can reach
  TCP 11730 on the server can control it, read along, or pose as the server, with or
  without this integration. Only the network can prevent that: keep the cinema server in
  its own VLAN, allow only Home Assistant to reach TCP 11730, and never expose it to the
  internet.
- **Control is off by default.** A newly added server is monitoring only until you enable
  "Allow playback control". The option covers every write path: play, pause, the
  scheduler switch, and the `play_spl` and `ingest_job` actions. Servers added before
  version 1.7.0 keep the setting they had.
- **Deleting content needs its own option.** Only the *Delete current composition* button
  can send DeleteCPL, and only when "Allow deleting content" is on. That option is off by
  default and separate from playback control. The button refuses while the show is
  playing, reads the loaded composition from the server at the moment of the press, and
  logs every deletion at warning level.
- **No command that stores content.** StoreCPL and StoreSPL are not part of the code, so
  they cannot be sent at all.
- **`raw_command` is read-only.** It accepts only commands that read state, and only the
  parameters that command takes.
- **Input is validated before it is sent.** Ids must be UUIDs, text has a length limit, and
  a value that does not fit its protocol field is refused without touching the connection.
- **Replies are untrusted.** Frames are limited to 8 MiB, every read has a timeout, a
  malformed frame drops the connection, and a reply of the wrong message type is refused.
- **`ingest_job` makes the server fetch content** from the address in the job description.
  Treat it like any other control action. If the description holds FTP credentials, note
  that Home Assistant keeps action data in automation traces.
- **No credentials are stored.** The config entry holds a host, a port and options.
- **Diagnostics are redacted.** Serial number, product id, address, film titles, show names
  and schedule annotations are left out.
- **No listening ports, no outbound connections.** Nothing is sent anywhere except to the
  server you configure, and the integration has no runtime dependencies.

Access to your Home Assistant instance with control enabled is equivalent to access to the
server's transport controls.

## Out of scope

- Vulnerabilities in Home Assistant itself — report those to the Home Assistant project.
- Vulnerabilities in the cinema server's firmware — report those to Dolby.
- The absence of authentication in the KLV protocol. That is the protocol's design, not a
  defect in this integration.
