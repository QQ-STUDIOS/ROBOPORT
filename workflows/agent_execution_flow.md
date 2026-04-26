# Agent Execution Flow

## Overview
Standard workflow for executing multi-agent tasks in ROBOPORT. Coordinates planner → executor → critic lifecycle with feedback loops.

## Flow Diagram
```
┌─────────┐
│  START  │
│  Task   │
└────┬────┘
     │
     v
┌─────────────┐
│  PLANNER    │  Create execution plan
│  Agent      │  - Decompose task
└─────┬───────┘  - Assign agents
      │          - Build DAG
      v
┌──────────────┐
│ ORCHESTRATOR │  Manage execution
│  Agent       │  - Initialize context
└──────┬───────┘  - Track dependencies
       │
       v
┌──────────────────────┐
│   EXECUTOR Agent     │  Execute steps
│  (Loop over steps)   │  - Validate I/O
└──────┬───────────────┘  - Invoke agents
       │                  - Capture metrics
       v
┌──────────────┐
│  Step N      │  Individual agent execution
│  Results     │
└──────┬───────┘
       │
       v
   ┌───────┐
   │Critic │  Review output
   │Agent  │  
   └───┬───┘
       │
       v
   Pass? ─────No────> Feedback ──┐
       │                          │
      Yes                         │
       │                          v
       v                    ┌──────────┐
  ┌────────┐               │  REVISE  │
  │ FINAL  │               │  Step    │
  │ OUTPUT │               └────┬─────┘
  └────────┘                    │
                                │
                         Retry (max 3)
                                │
                                v
                          Back to Executor
