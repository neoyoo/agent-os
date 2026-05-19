# Skill Progressive Disclosure

`agent-os` exposes skills in three layers:

1. Capability metadata stays in the context renderer through `SkillDeclaration`.
2. `load_skill(skill_name)` loads the primary `SKILL.md` body on demand.
3. `load_skill_resource(skill_name, path)` loads a skill-local resource only when
   the loaded skill asks for that detail.

The runtime must not require an LLM to read arbitrary files. `SkillContentSource`
is the SDK-owned ABC for this storage boundary. Local CLI agents can use the
default `FileSystemSkillSource`, while hosted agents can implement a Redis hot
cache backed by PostgreSQL, object storage, or another workspace skill service.

`SkillRegistry` is now an index and tool-facing facade. When backed by a
`SkillContentSource`, it delegates body and resource loading to that source so
SaaS implementations can keep hot skill bodies in Redis and durable versions in
PostgreSQL without exposing filesystem paths to the model.

Resource paths are skill-local and normalized. Path traversal and `SKILL.md`
resource loads are rejected; the skill body is only available through
`load_skill`.
