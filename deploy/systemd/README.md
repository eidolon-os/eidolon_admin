# Eidolon host-control systemd units

`eidolon-bootstrapd.service` is intentionally outside the supervisord-managed
application stack. It starts before `eidolon-stack.service`, does not depend on
`network-online.target`, and uses `Restart=always` with systemd as the only
restart authority. `WatchdogSec=30s` also restarts the process when its event
loop stops responding rather than only when the process exits.

The product image/provisioner must create:

- system user and group `eidolon-bootstrap`;
- no persistent membership of the shared `eidolon` user in `eidolon-bootstrap`;
  only `eidolon-local-api.service` receives that group through its unit-scoped
  `SupplementaryGroups=` setting, so the Admin/supervisord stack does not inherit
  control-socket access;
- manufacturing-provisioned `/var/lib/eidolon-bootstrap/host_identity.ed25519`
  with owner `eidolon-bootstrap:eidolon-bootstrap` and mode `0600`;
- install `deploy/polkit/60-eidolon-bootstrap-network.rules` under
  `/etc/polkit-1/rules.d/`; it grants only the NetworkManager actions used by
  the dedicated bootstrap process, including checkpoint/rollback for staged
  Wi-Fi changes;
- verify the target image's BlueZ system-bus policy permits the dedicated user
  to register the tracked GATT application. Do not add root or Linux
  capabilities as a workaround without recording the exact denied D-Bus call.
- install `deploy/avahi/eidolon-local-api.service` under
  `/etc/avahi/services/`; mDNS only discovers candidate addresses. Mobile still
  matches the Host ID and pins the Host-signed TLS SPKI before sending a
  Controller proof or bearer token. The current Uvicorn listener binds IPv4,
  so the Avahi service advertises `protocol="ipv4"`; do not advertise IPv6
  until Local API has a validated dual-stack listener.

Development uses an explicit drop-in that sets
`EIDOLON_BOOTSTRAP_MODE=development`; the tracked product unit always forces
`production` after reading its optional environment file. The tracked
`eidolon-bootstrapd-development.conf.example` is a template only and must not
be copied into product images.

The development Pi drop-in also selects the real `bluez` and `networkmanager`
adapters. Desktop/unit tests intentionally default to `disabled` + `memory`;
production settings fail closed unless both real adapters are selected.

Development may set `EIDOLON_BOOTSTRAP_DEV_SETUP_CODE` to one fixed six-digit
code in root-owned `/etc/eidolon/bootstrap.env` (mode `0600`). Bootstrap keeps
creating short-lived commissioning sessions for that code while the Host is
unclaimed. The setting is rejected in production mode and must never be placed
in the tracked systemd drop-in or a product image.

To repeat commissioning tests without deleting the Host identity, development
mode also exposes:

```text
eidolon-bootstrapctl dev reset
eidolon-bootstrapctl dev reset --forget-wifi
```

The first command increments `reset_epoch`, revokes Controller grants and Setup
sessions, and returns the Host to `unclaimed` while preserving its current
network. `--forget-wifi` additionally deletes all NetworkManager Wi-Fi profiles
and disconnects the Host, so the invoking SSH connection is expected to drop.
Both commands fail closed in production.

The commissioning TLS key is mode `0640`, owned by
`eidolon-bootstrap:eidolon-bootstrap`. The Local API receives that group only
through `SupplementaryGroups=` and terminates pinned HTTPS itself; the ordinary
Admin/supervisord processes do not inherit key access. The Bootstrap state
directory is mode `0710`: the group can traverse a known path but cannot list
the directory. The Host Ed25519 key and SQLite authority remain mode `0600` and
are never shared with Local API.

The runtime environment referenced by `eidolon-local-api.service` must include
the project's tested FastAPI and Uvicorn dependencies. A Bootstrap-only
development venv is not sufficient; validate imports as the `eidolon` service
user before enabling the unit.

Do not grant the existing Admin API process system privileges. Factory reset is
not implemented by these units and will use a separate root oneshot after every
service has supplied a reset manifest.
