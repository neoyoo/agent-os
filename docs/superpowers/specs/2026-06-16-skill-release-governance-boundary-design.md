# Skill Release Governance Boundary Design

## Target Conclusion

AgentOS is both an SDK and a developer guidance system. The repository copy of
the `agent-os` skill must not silently drift from an installed user-level copy,
but the SDK should not overwrite user skills, publish marketplace entries, or
own plugin installation.

AgentOS should expose a JSON-safe release manifest and drift report boundary so
CI, release scripts, or operators can compare the repository skill with an
installed copy before publishing or using it.

## Current Gap

The objective audit already marks SDK developer skill guidance as
`primitives-ready`, but its blocker is version synchronization:

- `.claude/skills/agent-os` is the repository source of skill guidance.
- user-level installed skills can lag or be locally edited.
- current docs mention the risk but do not provide an SDK-owned manifest or
  drift report shape.

Without a manifest, downstream release checks must invent their own file list,
hash strategy, and drift vocabulary.

## SDK Boundary

Add a new `agentos.skills` module with:

- `SkillReleaseManifest`
- `SkillReleaseFile`
- `SkillReleaseDriftReport`
- `build_skill_release_manifest(...)`
- `compare_skill_release_manifests(...)`

The boundary owns:

- deterministic relative file enumeration
- SHA-256 hashing
- file count and manifest hash
- JSON-safe `as_dict()` payloads
- missing/extra/changed file drift detection
- readiness-compatible drift report fields

The deployment owns:

- copying or installing the skill
- marketplace publishing
- plugin cache busting
- user approval for overwrites
- signing artifacts
- release notes
- CI policy for whether drift blocks release

## API Shape

`build_skill_release_manifest(skill_dir, version, source)` returns a manifest
with:

- `skill_name`
- `version`
- `source`
- `files`
- `file_count`
- `manifest_hash`

`compare_skill_release_manifests(expected, actual)` returns a drift report with:

- `ready`
- `status`
- `missing_files`
- `extra_files`
- `changed_files`
- `expected_version`
- `actual_version`
- `version_match`

The API must be deterministic, path-safe, and JSON-safe. It must reject empty
versions, empty sources, missing skill dirs, and directories without
`SKILL.md`.

## Non-Goals

- No writing to user-level skill directories.
- No marketplace publishing.
- No plugin install/uninstall.
- No signature authority.
- No release approval workflow.
- No changes to `QueryLoop`, `AsyncQueryLoop`, planner, A2A, or team runtime.
