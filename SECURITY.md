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
[GitHub Security Advisories](https://github.com/VideoBarista/dolby-ims3000-ha/security/advisories/new)
rather than in a public issue.

Expect an acknowledgement within a few days. This is a hobby project maintained in spare
time, so please allow reasonable time for a fix before public disclosure.

## Scope

This integration speaks the Doremi/Dolby KLV control protocol over a plain TCP socket on
your own network. Relevant security properties:

- **No authentication, no encryption.** The protocol offers neither. Anyone who can reach
  TCP 11730 on the server can control it, with or without this integration. Keep the
  cinema server on a separate VLAN and do not expose it to the internet.
- **Playback control is opt-in.** The integration is read-only until you enable
  "allow playback control" in the options.
- **The `raw_command` service sends arbitrary KLV commands.** It is deliberately powerful
  and is limited to the command table shipped with the integration. Treat access to your
  Home Assistant instance as equivalent to access to the server.
- **No credentials are stored.** The config entry holds a host, a port and timing options.
- **No outbound connections.** Nothing is sent anywhere except to the server you configure.

## Out of scope

- Vulnerabilities in Home Assistant itself — report those to the Home Assistant project.
- Vulnerabilities in the cinema server's firmware — report those to Dolby.
- The absence of authentication in the KLV protocol. That is the protocol's design, not a
  defect in this integration.
