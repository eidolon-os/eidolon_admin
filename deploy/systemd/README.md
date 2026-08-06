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
  the dedicated bootstrap process;
- verify the target image's BlueZ system-bus policy permits the dedicated user
  to register the tracked GATT application. Do not add root or Linux
  capabilities as a workaround without recording the exact denied D-Bus call.

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

Do not grant the existing Admin API process system privileges. Factory reset is
not implemented by these units and will use a separate root oneshot after every
service has supplied a reset manifest.
