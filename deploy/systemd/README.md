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
- narrowly scoped BlueZ/NetworkManager policy after the Phase 1 D-Bus calls are
  proven on Raspberry Pi 5.

Development uses an explicit drop-in that sets
`EIDOLON_BOOTSTRAP_MODE=development`; the tracked product unit always forces
`production` after reading its optional environment file. The tracked
`eidolon-bootstrapd-development.conf.example` is a template only and must not
be copied into product images.

Do not grant the existing Admin API process system privileges. Factory reset is
not implemented by these units and will use a separate root oneshot after every
service has supplied a reset manifest.
