"""butler 内核单测：全程离线，用假宿主与假模型注入，不联网、不依赖 HA。"""
from .decide import decide, fallback
from .engine import SAMPLES_ON_TRIAL, CycleReport, Engine, Outcome
from .models import (COMPUTED, EXTERNAL, INFERRED, OBSERVED, Action, Branch,
                     DecisionRequest, DecisionResponse, Judgment, Policy, Reading,
                     ReadingSpec, Safety, Shape, Snapshot, Verdict, median)
from .ports import (CompileFailed, DecisionProvider, ExternalContextProvider,
                    PlatformAdapter, PolicyCompiler, ProviderUnavailable)
from .samples import SAMPLE_POLICIES, sample_policy
from .trace import (COMPILE, COMPILE_SAVED, Tracer, compile_summary,
                     compile_trace_id, intent_head, new_trace_id)

__all__ = [
    "OBSERVED", "COMPUTED", "EXTERNAL", "INFERRED",
    "Reading", "Snapshot", "Shape", "Judgment", "Action", "Branch",
    "ReadingSpec", "Safety", "Policy", "DecisionRequest", "DecisionResponse",
    "Verdict", "median", "PlatformAdapter", "DecisionProvider",
    "ExternalContextProvider", "PolicyCompiler", "ProviderUnavailable",
    "CompileFailed",
    "decide", "fallback", "Engine", "CycleReport", "Outcome", "SAMPLES_ON_TRIAL",
    "Tracer", "new_trace_id", "compile_summary", "compile_trace_id",
    "intent_head", "COMPILE", "COMPILE_SAVED",
    "SAMPLE_POLICIES", "sample_policy",
]
