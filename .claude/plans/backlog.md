# CUGA FLO — TODO backlog

Unordered. Each is a heading of work, not a scoped plan yet.

- [ ] **Headless mode — CUGA FLO as a service.** Run without the web UI; drive flows over an
      API so another system can start a process, feed input, and read results.

- [ ] **Move CUGA FLO out of the branch into its own repo** under the cuga projects org,
      instead of living on the `cugaflo` branch of this repo.

- [ ] **Merge CUGA FLO Studio into CUGA FLO.** One codebase / one deployable instead of two.

- [ ] **Embed a process editor in the Studio** from Apache KIE — the visual BPMN editor
      component, wired into CUGA FLO Studio.

- [ ] **Prosimos integration — workflow simulation.** Wire in [Prosimos] as a simulator so a
      BPMN process (plus a simulation scenario) can be run statistically — arrival rates,
      resource contention, cycle-time / cost distributions — before or alongside live
      execution. [Prosimos]: https://github.com/AutomatedProcessImprovement/Prosimos

- [ ] **Rename `adapters/` → `plugins/`.** The workflow-engine adapters (`flowable`,
      `kogito`, …) are really pluggable backends; rename the package and the public
      terminology to "plugins", updating imports, docs, and `patches/` import paths.

- [ ] **READMEs for external / Apache KIE community exposure.** Rewrite the top-level and
      `docs/` READMEs for readers outside this project — clear positioning vs. plain KIE,
      quickstart that stands alone, and a KIE-developer-facing explanation of how CUGA FLO
      governs Kogito control points over MCP.

- [ ] **Context / memory sharing across agents.** Let the wrapper agents and any remote
      agents see shared context rather than each starting cold.

- [ ] **Handle process variables more rigorously.** Tighter validation, typing, and
      lifecycle for BPMN process variables end to end (YAML ↔ BPMN ↔ engine).
