# Raspberry Pi unified deployment route

Status: phase 1 runtime wiring implemented; full product release composition is not yet implemented.

## Evidence-backed current state

The inspected Kernel tree at `40d9b67602f428f848ce3c5b7f0471274086e93f` already contains:

- `deploy/systemd/eidolond.service`, `eidolon-data.service`, `eidolon-hub.service`, and `eidolon-kernel.service`;
- a systemd service manifest publishing the exact Data, Hub and Kernel contracts;
- a restricted Polkit rule allowing the `eidolon` identity inside `eidolond.service` to manage only those three units;
- `eidolon-release seal|activate|rollback`, immutable receipts, exclusive activation, fixed system-asset allowlists, readiness checks and rollback snapshots.

The same code and its runbook explicitly constrain release descriptor V1 to Kernel, Data and SDK. Hub, Admin and Bootstrap are outside that transaction.

The Admin product integration retains reviewed `eidolon-bootstrapd.service` and `eidolon-local-api.service`, secure BLE commissioning and a pinned-HTTPS, Controller-authenticated Local API. Its ADR explicitly classifies Local API as product ingress and the Admin operator API as a separate loopback/support-mode process. Those units use `/opt/eidolon/current/eidolon_admin`; host lifecycle and release paths are owned by `eidolon_ops`.

The former Data V2/Admin boundary and Bootstrap/Local API branches shared base `c553872` and overlapped in 35 changed files. Integration commit `06e7a2e` resolved those files semantically: Bootstrap, Local API and the independent audit projection remain, while Data ORM/SQLite aggregation, the Data-reading Memory runner, Mission Control cross-authority aggregation and their legacy Web/API surfaces remain deleted. A complete one-command Raspberry Pi deployment still does not exist because release composition and product ingress are not implemented.

## Target dependency and process shape

```text
product ingress / operator identity
    -> Admin API + built Admin Web
    -> eidolond directory over /run/eidolon/system.sock
    -> Data / Hub / Kernel public application contracts

systemd (PID/cgroup/signal executor)
    -> eidolond.service (only enabled desired-state manager)
    -> Data / Hub / Kernel units (not independently enabled)
```

Admin must not gain systemd authority merely to perform a Device Admission workflow. Deployment activation remains a separate root-operator boundary; runtime desired state remains eidolond-owned.

## Unified command without collapsing safety boundaries

The final operator surface should be one command with internally explicit phases:

```text
eidolon-ops --config config/hosts/pi5.toml deploy
  1. local/CI preflight and revision lock
  2. target-native aarch64 environment preparation (network allowed, non-root)
  3. contract tests and source/environment fingerprints
  4. transfer into a unique /opt/eidolon/releases/<release_id>
  5. target seal
  6. root activation dry-run
  7. explicit activation
  8. directory/API/readiness/reboot evidence
```

“One command” must not mean one opaque transaction. The dry-run receipt, activation receipt, snapshot path and exact component revisions remain visible and machine-readable. Secrets are provisioned separately as root-owned `0600` files and never enter Git, the release descriptor, transfer logs or rollback snapshots.

## Delivery phases

### Phase 1 — runtime wiring (implemented)

- macOS/dev manifest publishes Data, Hub and Kernel with exact contract identities;
- isolated Admin `os-control-plane` profile generates non-production credentials and databases;
- supervisord child authorities use `autostart=false`; eidolond owns desired state;
- preparation validates Data V2 migration/schema, contract drift, executable presence and supervisor syntax;
- real-process validation exercises Admin + eidolond + Data + Hub + Kernel and then stops all processes.

### Phase 2 — complete target release descriptor

Extend the existing `eidolon_deploy` descriptor rather than creating an Admin-owned competing activator:

- use the integrated Admin source that preserves Bootstrap's independent authority while keeping old Data/Memory/cross-authority aggregation deleted;
- add reviewed Hub and Admin components with full Git revisions, lock/environment/source fingerprints and fixed entrypoints;
- add an Admin systemd unit and a reviewed production-web/ingress ownership decision;
- add Admin and Hub readiness checks to activation and rollback;
- keep Data migration semantics explicit. A release that changes an authority schema cannot reuse V1's `database_migrations=[]` rollback claim;
- keep Bootstrap/onboarding as a separately reviewed always-on boundary unless its owner deliberately joins the release contract.

### Phase 3 — preparation and transfer driver

Add a non-root driver that prepares target-native environments and transfers a fixed release directory. It must use a revision lock, reject dirty sources, avoid forwarding Git/package credentials to root, and resume by content fingerprint rather than re-running arbitrary shell fragments.

### Phase 4 — first-install and upgrade commands

Separate destructive first-install operations from normal upgrades:

- `bootstrap`: create the service user/directories, install initial units/Polkit/config, provision secrets, create a fresh Data V2 database, and enable only eidolond;
- `deploy`: stage, seal, dry-run and activate an immutable application release;
- `rollback`: load a specific prior snapshot and verify recovered readiness;
- `doctor`: read-only host, architecture, disk, clock, network, secret-mode, SQLite and systemd checks.

Normal `deploy` must never silently recreate a database, rotate credentials, or perform first-install host mutations.

## Current blockers before product deployment

1. Data exposes one shared Companion Authority token; independent Admin/Kernel credentials require a producer-owned multi-consumer credential model.
2. Admin is trusted-local and has no product ingress/operator authentication middleware. Hub protects its own mutation, but that is not sufficient if Admin becomes remotely reachable.
3. Admin systemd/static-web serving and release ownership are not defined.
4. Release descriptor V1 excludes Hub/Admin and rejects all database migrations.
5. Global audit projection and high-frequency telemetry contracts remain absent; they are not deployment blockers for the narrow Device Admission control plane, but the removed cockpit must not be restored through database scanning.
6. Integration commit `06e7a2e` is not yet merged into the product source branch; a release must pin the reviewed integration revision rather than either pre-integration branch.
