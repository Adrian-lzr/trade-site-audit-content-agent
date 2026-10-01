# Production Acceptance Gate

`scripts/production_readiness.py` is a read-only release gate. It converts the
P0/P1 acceptance conditions in `D:\trade_visibility_agent_plan_v1.md` into a
machine-readable check of operator-supplied evidence. The command does not
contact a site, provider, CRM, Docker daemon, Git remote, CMS, CI system, or
deployment endpoint. It never publishes or changes application state.

## Run It

With no input the command is intentionally blocked:

```powershell
python scripts/production_readiness.py
```

Use a JSON manifest supplied by an authorized operator:

```powershell
python scripts/production_readiness.py --input path\to\production-evidence.json `
  --output output\production-readiness.json
```

CI can provide the manifest through any of these variables (the first
non-empty variable wins):

- `PRODUCTION_READINESS_EVIDENCE_JSON`
- `PRODUCTION_READINESS_EVIDENCE_FILE`
- `PRODUCTION_READINESS_EVIDENCE`
- `PRODUCTION_ACCEPTANCE_EVIDENCE_JSON`
- `PRODUCTION_ACCEPTANCE_EVIDENCE_FILE`
- `PRODUCTION_ACCEPTANCE_EVIDENCE`

Each value is JSON or a path to a JSON file. Individual checks may also be
overlaid with `PRODUCTION_PROVIDER_EVIDENCE`,
`PRODUCTION_AUTHORIZED_SITE_EVIDENCE`, `PRODUCTION_HUMAN_ANNOTATION_EVIDENCE`,
`PRODUCTION_BACKUP_RESTORE_EVIDENCE`, `PRODUCTION_IMAGE_BUILD_EVIDENCE`,
`PRODUCTION_IDENTITY_EVIDENCE`, `PRODUCTION_IDENTITY_MODE`,
`PRODUCTION_ROLLBACK_EVIDENCE`, `PRODUCTION_CRM_EVIDENCE`, and
`PRODUCTION_REMOTE_RELEASE_ADAPTER_EVIDENCE`. Individual evidence variables
must contain JSON records; a bare path or URL is not treated as a passing
result.

The process exits `0` only when every required check passes. A blocked gate
exits `1`, so a CI job cannot accidentally continue after missing evidence.

## Evidence Contract

The manifest root can contain an `evidence` object or place these keys at the
root. The following is a schema example, not real evidence. The angle-bracket
values must be replaced by references to artifacts produced by the authorized
environment; copying local fixture output into these fields does not make a
production check pass.

```json
{
  "mode": "production",
  "evidence": {
    "provider": {
      "evidence_type": "real",
      "owner": "<responsible person or team>",
      "source": "<authorized provider run>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<provider report path or URL>",
      "available": true
    },
    "authorized_site": {
      "evidence_type": "real",
      "owner": "<site owner>",
      "source": "<authorization record>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<authorization artifact>",
      "authorized": true,
      "scope": "read"
    },
    "annotations": {
      "evidence_type": "real",
      "owner": "<review lead>",
      "source": "<human annotation export>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<annotation report>",
      "required": 30,
      "completed": 30,
      "status": "complete"
    },
    "backup_restore": {
      "evidence_type": "real",
      "owner": "<operations owner>",
      "source": "<backup and restore run>",
      "verified_at": "<ISO-8601 timestamp>",
      "backup": {"status": "passed", "evidence_ref": "<backup artifact>"},
      "restore": {"status": "passed", "evidence_ref": "<restore verification>"}
    },
    "image_build": {
      "evidence_type": "real",
      "owner": "<build owner>",
      "source": "<CI image build>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<immutable build log or digest>",
      "docker_available": true,
      "built": true,
      "status": "passed"
    },
    "identity": {
      "mode": "required",
      "evidence_type": "real",
      "owner": "<identity owner>",
      "source": "<identity gateway configuration>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<configuration or verification record>",
      "status": "verified"
    },
    "rollback": {
      "evidence_type": "real",
      "owner": "<release owner>",
      "source": "<production rollback rehearsal>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<rollback report>",
      "executed": true,
      "verified": true
    },
    "crm": {
      "evidence_type": "real",
      "owner": "<analytics owner>",
      "source": "<authorized CRM export>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<CRM evidence>",
      "available": true,
      "authorized": true,
      "status": "passed"
    },
    "remote_release_adapter": {
      "evidence_type": "real",
      "owner": "<release owner>",
      "source": "<CMS/CI integration verification>",
      "verified_at": "<ISO-8601 timestamp>",
      "evidence_ref": "<integration test report>",
      "target": "<remote CMS or CI target>",
      "remote": true,
      "write_scope": true,
      "status": "verified"
    }
  }
}
```

For every production record, `evidence_type` must be exactly `real`, and
`owner`, `source`, and an ISO-8601 `verified_at` are required. A reference is
also required (`evidence_ref`, `ref`, `path`, `uri`, or `url`). A record marked
`synthetic`, `fixture`, `demo`, `mock`, or `is_synthetic: true` is reported as
synthetic and cannot pass a required check. Unknown provenance is blocked; the
gate never infers that a missing field is real.

The site authorization check proves that the target is authorized for the
evidence being reviewed. It does not grant publishing rights. The independent
`remote_release_adapter` check requires a real, verified CMS/CI/deployment
target with explicit write scope. Local Git, a deployment callback stored only
in this repository, `not_configured`, and read-only authorization all remain
blocked.

## Checks and Output

Required checks are:

- P0: authorized site evidence, human annotation completion, backup/restore,
  Docker image build, production identity mode, rollback, and remote release
  adapter.
- P1: one available real provider and authorized real CRM/inquiry evidence.

Search Console evidence is optional because the plan requires a separately
authorized site and does not claim it when absent. If a `search_console` record
is supplied, it is checked with the same real-provenance rules.

The JSON report has this shape:

```json
{
  "status": "blocked",
  "passed": ["authorized_site_evidence"],
  "blocked": ["required_real_provider", "crm_evidence"],
  "gaps": ["required_real_provider: ...", "crm_evidence: ..."],
  "checks": [],
  "read_only": true,
  "external_side_effects": {
    "provider_calls": false,
    "site_requests": false,
    "crm_requests": false,
    "docker_commands": false,
    "release_or_publish": false
  }
}
```

`passed` and `blocked` contain check IDs. `gaps` contains the reason for each
blocked ID. Each item in `checks` also records priority, status, provenance
kind, references, and a human-readable reason. `not_evaluated` is separate
from `passed`; optional evidence is never silently counted as a pass.

The repository's existing fixture provider, synthetic demo site, local Git
readiness rehearsal, static Compose check, and local callback reports are
valid development evidence only. Supplying them to this command will keep the
corresponding production check blocked, as intended. Run the actual provider,
authorized site, CRM, backup/restore, image build, identity, remote release,
and rollback procedures in their approved environments and record their
outputs before attempting a production release.
