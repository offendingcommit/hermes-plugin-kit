## Purpose

Consumer-facing test support shipped inside the installed package, so a plugin
author can prove their plugin still satisfies the Hermes registration contract
and learn when the host's own signatures have drifted underneath them.

## ADDED Requirements

### Requirement: Harness is importable from an installed wheel

The harness SHALL be importable from an installed distribution through the
package's public path, without adding a runtime dependency and without
importing Hermes or a test framework at module import time.

#### Scenario: Import from a clean install

- **WHEN** the built wheel is installed into an environment that has neither
  hermes-agent nor a test framework available
- **THEN** importing the harness through the public package path succeeds

#### Scenario: Import performs no host or test-framework import

- **WHEN** the harness module is imported
- **THEN** no hermes-agent module and no test framework has been imported as a
  side effect

### Requirement: Recorded calls replay against the real host signatures

The fake plugin context SHALL record the registration calls a plugin makes, and
those recorded calls SHALL be replayed against the real Hermes `PluginContext`
signatures wherever a Hermes checkout is importable.

#### Scenario: Host adds a required parameter

- **WHEN** a plugin registers against a host whose registrar has gained a
  required parameter
- **THEN** the replay fails and names the missing parameter

#### Scenario: Host renames a parameter

- **WHEN** a plugin registers against a host whose registrar has renamed a
  parameter the plugin passes
- **THEN** the replay fails and names the unexpected keyword

#### Scenario: Host removes a registrar

- **WHEN** a plugin registers a surface the host no longer exposes
- **THEN** the replay fails and names the missing registrar

#### Scenario: Host is unchanged

- **WHEN** a plugin registers against the real host with no drift
- **THEN** the replay reports clean and the recorded registration is available
  for assertion

### Requirement: Absent host is reported, never assumed clean

The harness SHALL remain usable with no Hermes checkout present, and a run that
could not perform the replay SHALL report that fact rather than presenting
itself as drift-checked. It SHALL name the revision a consumer must provision
to enable the replay.

#### Scenario: No checkout available

- **WHEN** the harness runs with no importable Hermes checkout
- **THEN** the fake still records calls, the result states that no replay was
  performed, and the required revision is named

### Requirement: Registration assertions are reusable

The harness SHALL expose assertions for the registration receipt's stable log
field order, duplicate detection, deterministic ordering, and the
schema-under-`function.parameters` convention, so a consumer asserts them
without copying literals.

#### Scenario: Receipt field order is pinned to the log order

- **WHEN** a receipt is asserted whose fields are emitted in a different order
  than the logged contract
- **THEN** the assertion fails

#### Scenario: Duplicate registration is rejected

- **WHEN** a plugin registers two tools under the same name
- **THEN** the assertion fails, while the same name used for both a slash
  command and a CLI command is accepted

#### Scenario: Flattened tool schema is rejected

- **WHEN** a tool schema declares its arguments at the top level instead of
  under `function.parameters`
- **THEN** the assertion fails

### Requirement: Documented public names are reachable

Every public name the kit documents SHALL be reachable through the package's
public import path, and a guard SHALL fail when a documented name is not.

#### Scenario: Documented name missing from the public list

- **WHEN** a name the documentation presents as public is absent from the
  package's export list
- **THEN** the guard fails and names it

#### Scenario: Reachability is proven against the built artifact

- **WHEN** the wheel is built and installed into a throwaway environment
- **THEN** every documented public name imports from that installation
