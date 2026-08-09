# ADR-0004: Mobile Device onboarding through the Controller Local API

Status: accepted

## Decision

The Mobile client never chooses an Owner or Device identity and never receives
a Hub management credential. A Controller-authenticated Local API session is
the only Mobile entry point. Admin executes a forward-only distributed
workflow:

1. Local API derives `owner_id` and `controller_id` from the reset-bound
   Controller session.
2. Admin mints a 60-second, Owner-scoped Hub JWT with only the
   `device-manager` role.
3. Hub consumes the physical `enrollment_id` + `pairing_secret` claim and
   returns the authoritative `device_id`.
4. Admin mounts that returned Device into the Owner in Kernel.
5. If requested, Admin attaches the mounted Device to the Companion.

There is no rollback across authorities. The `setup_id` and Mobile
`request_id` deterministically produce bounded child request IDs for Hub,
Kernel mount and Companion attach. Retrying the same request therefore resumes
forward from a safe committed state; changing its input is rejected by the
producer idempotency contracts.

The pairing secret is transient request material. It is represented as a
secret value, is not written to Admin state, and is never returned.

## Hub TLS identity source

The source of truth is the **Hub TLS leaf certificate actually configured on
the Hub's LAN HTTPS listener**. It is not mDNS, a QR payload, a descriptor
response, TOFU, or a separately copied fingerprint.

The installation supplies the public leaf PEM to Local API through
`EIDOLON_LOCAL_API_HUB_TLS_CERTIFICATE`, together with:

- `EIDOLON_LOCAL_API_HUB_ID`
- `EIDOLON_LOCAL_API_HUB_DESCRIPTOR_URI`

At Local API startup, Admin requires the three values together, requires the
descriptor URI to be the plain HTTPS v1 descriptor path, verifies that the URI
hostname/IP appears in the certificate SAN and that the certificate is
currently valid, then computes:

`sha256:<unpadded base64url(SHA-256(DER SubjectPublicKeyInfo))>`

The result is exposed as `tls_spki_fingerprint`. Certificate rotation is one
atomic deployment change: replace the Hub listener certificate and the public
PEM consumed by Local API, then restart both processes. A stale or mismatched
certificate fails closed before a target is returned.

Hub pairing authorization has a separate source. Admin reads the installation
secret `EIDOLON_ADMIN_HUB_MANAGEMENT_JWT_SECRET`, matching Hub's
`EIDOLON_HUB_MANAGEMENT_JWT_SECRET`, and uses it only to mint short-lived
Owner-scoped JWTs inside the Admin process.

## Mobile contracts

Both routes require `Authorization: Bearer <Controller session>` and require an
Owner-bound Host Workspace.

### `GET /api/local/v1/device-onboarding/target`

Response (`200`):

```json
{
  "operation": "local.device-onboarding-target",
  "contract_version": "1",
  "hub_id": "hub-local",
  "descriptor_uri": "https://eidolon-hub.local/api/device-onboarding/v1/descriptor",
  "tls_spki_fingerprint": "sha256:<43 unpadded base64url characters>"
}
```

The route returns `409` before Workspace initialization and `503` when the
installation has no complete, verified Hub TLS target.

### `PUT /api/local/v1/device-admissions/{setup_id}`

Request:

```json
{
  "contract_version": "1",
  "request_id": "device-pair-claim-1",
  "hub_id": "hub-local",
  "descriptor_uri": "https://eidolon-hub.local/api/device-onboarding/v1/descriptor",
  "enrollment_id": "enrollment_<24 base64url chars>",
  "pairing_secret": "<43 base64url chars>",
  "companion_id": "companion_primary"
}
```

`hub_id` and `descriptor_uri` must exactly match the target returned by this
Host. `owner_id` and `device_id` are forbidden fields. The response always
describes the furthest safe stage reached for downstream partial completion:

```json
{
  "operation": "local.device-admission-progress",
  "contract_version": "1",
  "setup_id": "device-pair-1",
  "request_id": "device-pair-claim-1",
  "device_id": "device-authoritative-from-hub",
  "enrollment_id": "enrollment_<24 base64url chars>",
  "owner_id": "owner-derived-from-controller",
  "state": "ready",
  "completed_stage": "companion-attached",
  "companion_id": "companion_primary",
  "retryable": false
}
```

Possible progress states are `approved`, `binding`, `ready`, and `failed`;
possible completed stages are `hub-approved`, `kernel-mounted`, and
`companion-attached`. Producer authentication/identity failures remain HTTP
errors. Retryable Kernel partial completion returns HTTP `200` with
`retryable: true`, so Mobile can retain the pairing proof and repeat the exact
PUT without interpreting an infrastructure status code as rollback.

The machine-readable contracts live in `contracts/local-api/v1`.
