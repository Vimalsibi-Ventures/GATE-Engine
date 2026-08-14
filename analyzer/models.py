from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

@dataclass
class Signal:
    name: str
    category: str
    width: Optional[str] = None
    driven_by: Optional[str] = None
    instance: Optional[str] = None
    resolved_module: Optional[str] = None
    resolved_role: Optional[str] = None
    alias_expr: Optional[str] = None

@dataclass
class ConditionNode:
    condition_id: str
    file: str
    module: str
    always_block: str
    line: int
    column: int
    condition: str
    effective_condition: str
    normalized_condition: str
    path_conditions: List[str]
    fan_in: List[Signal]
    classification: str
    reason: str
    likely_reset_related: bool = False

@dataclass
class RegisterAssignment:
    register: str
    value: str
    condition: str
    always_block: str
    line: int

@dataclass
class FSMInfo:
    register: str
    states: List[str]
    transitions: List[Dict[str, Any]]

@dataclass
class SequentialBlockMeta:
    always_block: str
    clock: Optional[str]
    reset: Optional[str]
    edge: str
    start_line: int
    end_line: int
    raw_sensitivity: str

@dataclass
class Instance:
    instance_name: str
    child_module: str
    parent_module: str
    port_map: List[Dict[str, Optional[str]]]
    line: int

@dataclass
class ModuleInfo:
    name: str
    ports: Dict[str, str]
    registers: List[str]
    fsm_states: List[str]
    parameters: List[str]
    sequential_blocks: List[SequentialBlockMeta] = field(default_factory=list)
    instances: List[Instance] = field(default_factory=list)