# Eidolon host-control systemd units

`eidolon-admin.service` runs the operator control plane as the unprivileged
`eidolon` user and binds only to `127.0.0.1:9000`. It resolves Data, Hub and
Kernel through `/run/eidolon/system.sock`; it does not receive Bootstrap socket
access or open producer databases. Product remote access requires a separately
authenticated ingress and is deliberately not supplied by this unit.

Device removal is not sent to that loopback API. `eidolon-local-api.service`
runs as the dedicated `eidolon-local-api` principal and calls the single-purpose
`eidolon-lifecycle-workflow.service`, running as `eidolon-lifecycle`, through
`/run/eidolon-lifecycle/workflow.sock`. The runtime directory is
`eidolon-lifecycle:eidolon-lifecycle-client` mode `0750`; the socket is mode
`0660`. The unit selects the socket-only group as its process primary group, so
systemd creates the directory with its final ACL and the socket inherits that
group at bind time. The account's own primary group remains unchanged and is
added only to the unit's supplementary groups. No startup hook changes owner,
group, or mode after the service sandbox is active. Local receives only the
dedicated client group through `SupplementaryGroups=`. The Workflow state
directory is separately mode `0700`
and its SQLite/WAL files are created under `UMask=0077`, so socket access never
grants ledger access. The Workflow also
checks the accepted connection's Linux `SO_PEERCRED` UID against the installed
Local account before reading the request. Socket ACLs are not authentication.

Admin's removal-capability broker follows the same rule: its runtime directory
is `eidolon:eidolon-lifecycle-client` mode `0750`, while Admin retains its
ordinary `eidolon` group only as a supplementary group. `NoNewPrivileges`, an
empty capability bounding set, and `RestrictSUIDSGID` stay enabled; shared
socket setup must never require a privileged `ExecStartPre` or a target-only
drop-in. Admin is a systemd `Type=notify` service and declares readiness only
after this broker has bound its socket. Lifecycle Workflow's `Requires=` and
`After=` dependency therefore waits on an application-level readiness boundary,
not merely the creation of the Admin process. This keeps cold start independent
of release-tool ordering and avoids retry delays that could conceal a broken
broker.

`eidolon-bootstrapd.service` is intentionally outside the supervisord-managed
application stack. It starts before `eidolon-stack.service`, does not depend on
`network-online.target`, and uses `Restart=always` with systemd as the only
restart authority. `WatchdogSec=30s` also restarts the process when its event
loop stops responding rather than only when the process exits.

The product image/provisioner must create:

- system users and primary groups `eidolon-bootstrap`, `eidolon-local-api`, and
  `eidolon-lifecycle`;
- the socket-only system group `eidolon-lifecycle-client`; it is not an owner of
  Workflow state;
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

`EIDOLON_BOOTSTRAP_DEV_SETUP_CODE` no longer exists. It pinned one fixed Setup
code so a workstation loop would not have to reprint it, and no Host, no ops
profile and no CI job ever set it — while an unclaimed Host minted a session
out of that code every time anything merely *read* its commissioning endpoint,
so the window was permanently open and the one-time code was neither. Mint a
code when you need one:

```text
eidolon-bootstrapctl commissioning-code [--ttl SECONDS] [--code DIGITS]
eidolon-bootstrapctl commissioning-status
```

`--code` names the value instead of letting the Host draw one, which is how a
development loop gets its convenience back without the mechanism changing: the
code opens one ordinary session that expires, is spent once, dies after five
wrong tries and supersedes any window before it. Operators normally set it once
per Host in the ops profile (`app.setup_code`) rather than typing it. There is
deliberately no mode check on this — whoever reaches this socket can already
mint a random code and read it back, and a shipped Host will one day need to be
told the code printed on its own box. What is enforced, however the code
arrives, is that it is one this Host would have drawn itself.

`commissioning-status` says whether a claim window is open, and never the code.

A window exists if and only if an operator minted one — on every Host, in
every mode.

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

The product provisioner must also create root-owned `admin.env` and
`local-api.env` files with mode `0600`. The legacy Local-to-Admin credential
remains limited to non-removal routes during the wider P6 cutover; the removal
path never reads or transmits it. Lifecycle Workflow has no static Local or Hub
credential. Its only Hub authority is the peer-authenticated, fixed-operation
broker socket owned by Admin; Hub's generic management signer never enters the
Workflow process. `admin.env` separately contains
`EIDOLON_ADMIN_DATA_WORKSPACE_AUTHORITY_TOKEN`, matching only Data's
`EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN`; neither credential is a Controller
session token or a Data read token.

Do not grant the existing Admin API process system privileges. Factory reset is
not implemented by these units and will use a separate root oneshot after every
service has supplied a reset manifest.
