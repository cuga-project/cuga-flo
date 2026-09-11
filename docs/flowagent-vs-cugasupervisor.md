# FlowAgent vs CugaSupervisor

Both are top-level orchestrators exposing `invoke()`, and `FlowAgent` keeps that shape — but it
deliberately drops the conversational machinery, because a BPMN process instance is a different
unit of work from a chat thread.

**Not carried over** — none of these appear in `FlowAgent`:

| Capability | In `CugaSupervisor` | Why it is absent |
|---|---|---|
| Threading / multi-turn | `thread_id`, auto-generated per conversation | The process instance is the unit of identity; `FlowAgent` keys on `process_key`. |
| Checkpointing | `MemorySaver`, `compile(checkpointer=…)` | State belongs to the engine — Flowable and Kogito persist it themselves. |
| Resume / HITL | `invoke(message=None, action_response=…)`, `update_state({"hitl_response": …})` | No pause-and-resume. See the gap noted below. |
| Dynamic agent registry | `add_agent()` / `remove_agent()`, graph rebuilt on next invoke | Task and gateway agents are fixed at construction from `FlowConfig`; the BPMN decides who runs where. |
| A2A external agents | agents may be an A2A config dict | Only in-process `TaskAgent` / `DecisionAgent`. |
| `variables_manager` | typed accessor over `supervisor_variables_manager` | Replaced by the flat `process_variables` dict, which is what engines exchange. |
| Callbacks | `List[BaseCallbackHandler]` threaded into graph config | No pass-through; `ActivityTracker` is the only instrumentation. |
| Step cap | `cuga_lite_max_steps` | The BPMN bounds execution structurally, so no runaway-loop guard is needed. |
| OpenLit hooks | `init_openlit()`, `set_session_attribute(thread_id)` | Not called — see the gap noted below. |

**The deepest difference is not in that table.** The supervisor node returns `Command(goto=…)`: an
LLM chooses which agent runs next, which is why its state carries `available_agents` and
`selected_agents`. `FlowAgent` never makes that choice — the engine does, and LLM reasoning is
confined to task fulfilment, gateway routing, and hook adaptation. That is the CUGA FLO thesis
rather than a reduction in capability.

**Kept in altered form:** `invoke()` (returns a `FlowState`, not an `InvokeResult`), YAML
construction (`FlowConfig.from_yaml` rather than `CugaSupervisor.from_yaml`), and per-agent
policies — `special_instructions` survives on the lazily-created hook agent.

**Two of these are genuine gaps rather than deliberate scoping:**

- **Human-in-the-loop.** `HookResult` carries a `user_prompt` field and the hook prompt asks the
  LLM for one, but nothing in `FlowAgent` can suspend and resume on a reply — so a hook cannot in
  fact stop for a human. The field is currently inert.
- **Observability.** Engine-driven runs never call `init_openlit()` or `set_session_attribute()`,
  so they do not appear in OpenLit traces, which the supervisor path gets for free.
