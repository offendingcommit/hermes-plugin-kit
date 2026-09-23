# CHANGELOG

<!-- version list -->

## v0.9.0 (2026-09-23)

### Bug Fixes

- Make the harness and drift lane environment agnostic
  ([`c9ef916`](https://github.com/offendingcommit/hermes-plugin-kit/commit/c9ef916b483f8b79891a04172d8af704d572f0b9))

- **ci**: Use the approved source-promotion token
  ([#38](https://github.com/offendingcommit/hermes-plugin-kit/pull/38),
  [`70f0246`](https://github.com/offendingcommit/hermes-plugin-kit/commit/70f0246da658376ed0d05b31de301050c1fab2f4))

- **plugins**: Close references_dir probe gaps found in review
  ([#27](https://github.com/offendingcommit/hermes-plugin-kit/pull/27),
  [`b56df77`](https://github.com/offendingcommit/hermes-plugin-kit/commit/b56df77caa6eabb3035a2957fad3cf5962987161))

- **plugins**: Stop claiming Hermes cannot serve companion files
  ([`7c65594`](https://github.com/offendingcommit/hermes-plugin-kit/commit/7c65594d573743a9a774429d7cb68ebcc52b5016))

- **release**: Gate releases on the deployed pin, not upstream
  ([#34](https://github.com/offendingcommit/hermes-plugin-kit/pull/34),
  [`f30a5fa`](https://github.com/offendingcommit/hermes-plugin-kit/commit/f30a5faefe224014050822946df878940c10442b))

- **release**: Read Hermes evidence from the pinned checkout directory
  ([`25f184e`](https://github.com/offendingcommit/hermes-plugin-kit/commit/25f184eba064ff3b8e486a504ec7debe47095a52))

- **skills**: Accept skill-relative reference paths
  ([#28](https://github.com/offendingcommit/hermes-plugin-kit/pull/28),
  [`e7bbcb2`](https://github.com/offendingcommit/hermes-plugin-kit/commit/e7bbcb2f0984404f1df0e5e7b4e93cd10ff48c28))

- **testing**: Catch positionally renamed host parameters
  ([`8827547`](https://github.com/offendingcommit/hermes-plugin-kit/commit/88275475bef7d665c3c1e8da1d85509c49132be4))

- **tests**: Assert the packaging manifest, not whatever is in dist/
  ([#35](https://github.com/offendingcommit/hermes-plugin-kit/pull/35),
  [`7b6b416`](https://github.com/offendingcommit/hermes-plugin-kit/commit/7b6b4169bd8968e268a8ff4489c8dacfc41d9802))

- **tests**: Find the host's companion-file routine wherever it lives
  ([`bda2c91`](https://github.com/offendingcommit/hermes-plugin-kit/commit/bda2c9146f524a80a834e4f3bf79be1d44bd5545))

- **tools**: Give supplied schemas the same description guidance as params
  ([#36](https://github.com/offendingcommit/hermes-plugin-kit/pull/36),
  [`da4ba9f`](https://github.com/offendingcommit/hermes-plugin-kit/commit/da4ba9f400141406976c1a3f4641ccb9a2bd140d))

### Chores

- **openspec**: Adopt OpenSpec and scaffold the three changes
  ([`9862e3b`](https://github.com/offendingcommit/hermes-plugin-kit/commit/9862e3bccb38de37640598bcb45e85d93e679580))

- **openspec**: Archive the four shipped changes
  ([`e14e18c`](https://github.com/offendingcommit/hermes-plugin-kit/commit/e14e18cf7e9be3ae870ccc3ba412d8a9e71b7f99))

- **openspec**: Resync Change A tasks with the revised plan
  ([`50a7041`](https://github.com/offendingcommit/hermes-plugin-kit/commit/50a704164dad46f2bd92759236a2f25c8012dff9))

### Documentation

- **openspec**: Author the author-routing change
  ([`f1eb84f`](https://github.com/offendingcommit/hermes-plugin-kit/commit/f1eb84f18b7adbe00b86b48c1e8ce94666458276))

- **openspec**: Author the contract-gate change
  ([`297f3d4`](https://github.com/offendingcommit/hermes-plugin-kit/commit/297f3d4783aac526716d131ca53ddf9ba5e6ecc1))

- **plans**: Fold in the remaining review findings
  ([`bd513eb`](https://github.com/offendingcommit/hermes-plugin-kit/commit/bd513ebbc6e5a5fd92457f1f6e0cb865bb8b76c1))

- **plans**: Plan the plugin contract testing harness
  ([`3efcfb1`](https://github.com/offendingcommit/hermes-plugin-kit/commit/3efcfb132555269edd23715ee87c4f87a1c9efbf))

- **plans**: Qualify KTD1's rename claim to match the shipped behavior
  ([`e5b62ae`](https://github.com/offendingcommit/hermes-plugin-kit/commit/e5b62ae4e3f97dcb9c394474ba6e90d9327a6378))

- **plans**: Record the settled provisioning, pin, and migration calls
  ([`2eda2f0`](https://github.com/offendingcommit/hermes-plugin-kit/commit/2eda2f0e4bbdf6ec54a4af1baabafc5ec6402682))

- **plans**: Resolve the cross-model findings and four scope calls
  ([`c75aa5e`](https://github.com/offendingcommit/hermes-plugin-kit/commit/c75aa5edd077063eae89fd40f9e230195d069ca2))

- **skills**: Hermes does serve companion files, by convention not by argument
  ([#37](https://github.com/offendingcommit/hermes-plugin-kit/pull/37),
  [`322ae79`](https://github.com/offendingcommit/hermes-plugin-kit/commit/322ae791f368911c72211a8da9308c02c8fc78dc))

- **skills**: Route plugin authors to the harness
  ([`e5a37da`](https://github.com/offendingcommit/hermes-plugin-kit/commit/e5a37daa2c8d3203062e6733e93e1e3fdfc6a5cc))

- **skills**: Trim skill frontmatter and document snapshot promotion
  ([`111057b`](https://github.com/offendingcommit/hermes-plugin-kit/commit/111057ba89d61d513608120e5313650e5294f748))

### Features

- **ci**: One discovery policy and an honest local contract lane
  ([`f0b4a73`](https://github.com/offendingcommit/hermes-plugin-kit/commit/f0b4a73e42d8191f7493cd64bbba90920fb9898d))

- **ci**: Widen the blocking gate, move drift to its own schedule
  ([`c4dbbb8`](https://github.com/offendingcommit/hermes-plugin-kit/commit/c4dbbb84ada7cd7e1ac41163af7a111c8b65fc3e))

- **packaging**: Guard the public surface and prove it from a wheel
  ([`77c610d`](https://github.com/offendingcommit/hermes-plugin-kit/commit/77c610d02318ef2947c586874e9e5a1e5b4ba2ea))

- **plugins**: Add forward-compatible references_dir for plugin skills
  ([#27](https://github.com/offendingcommit/hermes-plugin-kit/pull/27),
  [`b56df77`](https://github.com/offendingcommit/hermes-plugin-kit/commit/b56df77caa6eabb3035a2957fad3cf5962987161))

- **plugins**: Add plugin_reference_tool for agent-visible skill references
  ([#27](https://github.com/offendingcommit/hermes-plugin-kit/pull/27),
  [`b56df77`](https://github.com/offendingcommit/hermes-plugin-kit/commit/b56df77caa6eabb3035a2957fad3cf5962987161))

- **plugins**: References_dir + plugin_reference_tool for agent-visible skill references
  ([#27](https://github.com/offendingcommit/hermes-plugin-kit/pull/27),
  [`b56df77`](https://github.com/offendingcommit/hermes-plugin-kit/commit/b56df77caa6eabb3035a2957fad3cf5962987161))

- **testing**: Host shapes that reach the capability-probe branches
  ([`6dfaf2a`](https://github.com/offendingcommit/hermes-plugin-kit/commit/6dfaf2accae7a2bc567d66c32a1a5fd141ddd99e))

- **testing**: Record-and-replay plugin context for drift detection
  ([`f1e77ab`](https://github.com/offendingcommit/hermes-plugin-kit/commit/f1e77ab1e741b848d63b8e66ea40cd495c0e773a))

- **testing**: Registration, receipt, and schema check helpers
  ([`2088a6d`](https://github.com/offendingcommit/hermes-plugin-kit/commit/2088a6d8c4960feb6d4a593f3e6047934e60b9e7))

- **testing**: Ship a checkout resolver so the replay is reachable
  ([`f887d70`](https://github.com/offendingcommit/hermes-plugin-kit/commit/f887d70e376c3957a2c3765631eaaa03a1dd8725))

### Refactoring

- **tests**: Migrate the kit's own fake onto the shipped harness
  ([`2041373`](https://github.com/offendingcommit/hermes-plugin-kit/commit/2041373ad0f4c0c5fb3a6c4a5f5cb689b830054b))

### Testing

- **release**: Hold the contract-gate shape against regression
  ([`2d7773a`](https://github.com/offendingcommit/hermes-plugin-kit/commit/2d7773ae498ede8832d6365706df3fa703fd3265))


## v0.8.0 (2026-08-14)

### Bug Fixes

- **release**: Accept nested sdist metadata
  ([`005782e`](https://github.com/offendingcommit/hermes-plugin-kit/commit/005782e1cfc4a7a77f108dc45d8ff0d0ee1d858f))

- **release**: Attach exact trigger to main via Just
  ([`68fe9c3`](https://github.com/offendingcommit/hermes-plugin-kit/commit/68fe9c3312bc7d44fd63276a958c15b3eb902fb5))

- **release**: Harden publication boundaries
  ([#25](https://github.com/offendingcommit/hermes-plugin-kit/pull/25),
  [`6ba5700`](https://github.com/offendingcommit/hermes-plugin-kit/commit/6ba570010d9e808639c830154c41dab13cd41f80))

### Build System

- Replace Make with Just ([#25](https://github.com/offendingcommit/hermes-plugin-kit/pull/25),
  [`6ba5700`](https://github.com/offendingcommit/hermes-plugin-kit/commit/6ba570010d9e808639c830154c41dab13cd41f80))

### Features

- **release**: Add immutable SemVer publication lane
  ([#25](https://github.com/offendingcommit/hermes-plugin-kit/pull/25),
  [`6ba5700`](https://github.com/offendingcommit/hermes-plugin-kit/commit/6ba570010d9e808639c830154c41dab13cd41f80))


Release notes are generated from Conventional Commits by Python Semantic Release.
