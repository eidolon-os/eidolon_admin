# ADR-0004: Mobile approval of screen-independent Device enrollments

Status: accepted

## Decision

External Devices enroll themselves with Hub and remain `pending-approval` in
the `unclaimed` scope. They do not need a screen, QR code, pairing secret or a
Mobile-generated identity proof.

Mobile is the human confirmation surface:

1. a Controller-authenticated Local API session derives the Host Owner and
   Controller identities;
2. Local API returns Hub's bounded `unclaimed` pending directory;
3. the user selects a Device and taps **确认认领并绑定**;
4. Admin mints a 60-second `hub-admin` JWT that never leaves Admin;
5. Hub approves the selected Device into the derived Owner;
6. Admin mounts it in Kernel and optionally attaches it to the Companion.

The Controller session proves who is approving. Hub's pending directory is the
authority for what can be approved. The Owner is never accepted from Mobile.
Admin's internal path and body must carry the same Device ID.

There is no distributed rollback. A stable Mobile `request_id` produces
deterministic Hub, Kernel mount and Companion attachment request IDs. Retrying
continues forward from the furthest committed stage.

## Product contracts

Both routes require `Authorization: Bearer <Controller session>` and a Host
with a ready Owner Workspace.

### `GET /api/local/v1/device-enrollments/pending`

```json
{
  "operation": "local.pending-device-enrollments",
  "contract_version": "1",
  "devices": [{
    "device_id": "aa:bb:cc:dd:ee:ff",
    "display_name": "Living Room Device",
    "device_kind": "voice-client",
    "enrolled_at": "2026-08-09T10:00:00Z"
  }]
}
```

Only `owner_scope=unclaimed` and `lifecycle_state=pending-approval` entries are
accepted from Admin. Any cross-scope response fails closed.

### `POST /api/local/v1/device-enrollments/{device_id}/approval`

```json
{
  "contract_version": "1",
  "request_id": "device-approval-abc",
  "companion_id": "companion_primary"
}
```

`owner_id`, Hub credentials and arbitrary target URLs are forbidden. The
response reports the furthest safe stage:

```json
{
  "operation": "local.device-admission-progress",
  "contract_version": "1",
  "request_id": "device-approval-abc",
  "device_id": "aa:bb:cc:dd:ee:ff",
  "owner_id": "owner-derived-from-controller",
  "state": "ready",
  "completed_stage": "companion-attached",
  "companion_id": "companion_primary",
  "retryable": false
}
```

## Hub TLS identity

Direct Device enrollment still uses the verified Hub target returned by
`GET /api/local/v1/device-onboarding/target`. Its SPKI fingerprint is computed
from the Hub HTTPS listener's configured leaf certificate. It is unrelated to
human approval and is not copied from discovery data.

The machine-readable contracts live in `contracts/local-api/v1`.
