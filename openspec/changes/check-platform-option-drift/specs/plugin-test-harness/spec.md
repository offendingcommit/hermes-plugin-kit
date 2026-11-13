## MODIFIED Requirements

### Requirement: Recorded calls replay against the real host signatures

The fake plugin context SHALL record the registration calls a plugin makes, and
those recorded calls SHALL be replayed against the real Hermes `PluginContext`
signatures wherever a Hermes checkout is importable.

#### Scenario: Host adds a required parameter

- **WHEN** a plugin registers against a host whose registrar has gained a
  required parameter
- **THEN** the replay fails and names the missing parameter

#### Scenario: Host renames a keyword parameter

- **WHEN** a plugin registers against a host whose registrar has renamed a
  parameter the plugin passes by keyword
- **THEN** the replay fails and names the unexpected keyword

#### Scenario: Host renames a positional parameter

- **WHEN** a plugin registers against a host whose registrar has renamed a
  parameter the plugin passes positionally, leaving arity unchanged
- **THEN** the replay fails and names both the old and the new parameter name,
  rather than reporting clean because the call still binds by position

#### Scenario: Host removes a registrar

- **WHEN** a plugin registers a surface the host no longer exposes
- **THEN** the replay fails and names the missing registrar

#### Scenario: Host is unchanged

- **WHEN** a plugin registers against the real host with no drift
- **THEN** the replay reports clean and the recorded registration is available
  for assertion

#### Scenario: Host removes a platform option

- **WHEN** a plugin registers a platform with an option the host's
  `PlatformEntry` no longer has, and the replay is given the host's
  `PlatformEntry` field names
- **THEN** the replay fails and names `register_platform` and the missing
  option, even though the call still binds to the registrar's signature

#### Scenario: Platform options cannot be checked

- **WHEN** a plugin registers a platform and the replay is not given the host's
  `PlatformEntry` field names
- **THEN** the replay reports the run as not checked rather than clean, and
  still names any signature drift it found
