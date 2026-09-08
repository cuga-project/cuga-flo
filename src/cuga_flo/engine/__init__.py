"""
CUGA FLO - Flow-based Agentic Process Orchestration

This module implements the TDF Model (Task-Decision-Flow) for BPMN-based
agentic applications in CUGA.

Agent Types:
- FlowAgent: Meta-agent that orchestrates BPMN process execution
- DecisionAgent: LLM-powered gateway routing agent
- TaskAgent: Task execution agent (wrapper around CugaAgent)

Key Components:
- BPMN Parser: Converts BPMN XML to LangGraph StateGraph
- Hook System: Runtime interception and graph reformation
- Flow State: Extended state management for process execution
"""

from cuga_flo.engine.flow_agent_state import (
    FlowState,
    GraphModification,
)
from cuga_flo.engine.hook_manager import (
    Hook,
    HookType,
    HookAction,
    HookResult,
    HookManager,
)
from cuga_flo.engine.bpmn_parser import (
    BPMNParser,
    BPMNProcess,
    BPMNElement,
    BPMNFlow,
)
from cuga_flo.engine.decision_agent import DecisionAgent
from cuga_flo.engine.task_agent import TaskAgent
from cuga_flo.engine.flow_agent import FlowAgent
from cuga_flo.engine.flow_config import FlowConfig, load_flow_from_yaml
from cuga_flo.engine.process_registry import (
    ProcessRegistry,
    ProcessDefinition,
)
from cuga_flo.engine.workflow_engine import (
    WorkflowEngine,
    ControlPointFlowKnowledge,
)
from cuga_flo.engine.langgraph_engine import LangGraphWorkflowEngine
from cuga_flo.mcp import MCPFlowBridge

__all__ = [
    # State
    "FlowState",
    "GraphModification",
    # Hooks
    "Hook",
    "HookType",
    "HookAction",
    "HookResult",
    "HookManager",
    # BPMN Parser
    "BPMNParser",
    "BPMNProcess",
    "BPMNElement",
    "BPMNFlow",
    # Agents
    "DecisionAgent",
    "TaskAgent",
    "FlowAgent",
    # Configuration
    "FlowConfig",
    "load_flow_from_yaml",
    # Registry & Engine (new)
    "ProcessRegistry",
    "ProcessDefinition",
    "WorkflowEngine",
    "ControlPointFlowKnowledge",
    "MCPFlowBridge",
    "LangGraphWorkflowEngine",
]

# Made with Bob
