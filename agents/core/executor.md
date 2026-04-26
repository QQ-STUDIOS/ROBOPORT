# Executor Agent

## Purpose
Executes individual plan steps by invoking appropriate agents, managing tool calls, and handling execution state. Core runtime component of the ROBOPORT agent-os.

## Role
Task Execution Engine

## Input Type
```typescript
{
  step: {
    step_id: string;
    description: string;
    agent_type: string;
    input_spec: object;
    output_spec: object;
    tools_required?: string[];
  };
  context: object;              // Input data for this step
  execution_config?: {
    timeout_ms?: number;
    retry_policy?: {
      max_attempts: number;
      backoff_ms: number;
    };
    model_override?: string;
  };
}
```

## Output Type
```typescript
{
  step_id: string;
  status: "success" | "failed" | "timeout" | "skipped";
  result?: object;              // Step output (if success)
  error?: {
    type: string;
    message: string;
    recoverable: boolean;
  };
  execution_metrics: {
    start_time: number;
    end_time: number;
    duration_ms: number;
    attempts: number;
    tokens_used?: number;
    tool_calls?: number;
  };
  artifacts?: {
    [key: string]: string;      // Generated files, logs, etc.
  };
}
```

## Operating Rules

### 1. Agent Invocation
- Load agent definition from registry by agent_type
- Apply input_spec validation before invocation
- Apply output_spec validation after completion
- Use execution_config.model_override if provided, else agent's default

### 2. Error Handling
- **Timeout**: Kill execution after timeout_ms, return status="timeout"
- **Validation Failure**: Input/output mismatch → recoverable error
- **Tool Failure**: Tool returns error → depends on tool's side_effects flag
- **Agent Crash**: Unhandled exception → non-recoverable error

### 3. Retry Logic
```
attempt = 0
while attempt < max_attempts:
  result = invoke_agent(step, context)
  if result.status == "success":
    return result
  if not result.error.recoverable:
    return result  # Don't retry non-recoverable errors
  attempt += 1
  sleep(backoff_ms * 2^attempt)  # Exponential backoff
return result  # Max attempts reached
```

### 4. State Management
- Capture all execution artifacts (logs, intermediate files, API responses)
- Store metrics (duration, token usage, tool call count)
- Preserve error context for debugging
- Support resumption (can retry failed step without re-running entire workflow)

## Execution Patterns

### Synchronous Execution
```python
def execute_step(step, context, config):
    start = time.now()
    try:
        agent = load_agent(step.agent_type)
        validated_input = validate(context, step.input_spec)
        result = agent.run(validated_input)
        validated_output = validate(result, step.output_spec)
        return ExecutionResult(
            step_id=step.step_id,
            status="success",
            result=validated_output,
            execution_metrics={
                "duration_ms": time.now() - start,
                "attempts": 1
            }
        )
    except TimeoutError:
        return ExecutionResult(status="timeout", ...)
    except ValidationError as e:
        return ExecutionResult(
            status="failed",
            error={"type": "validation", "message": str(e), "recoverable": True},
            ...
        )
```

### Tool Call Interception
```python
def execute_with_tool_monitoring(step, context):
    tool_calls = []
    
    def monitor_tool(tool_name, tool_input):
        tool_calls.append({"tool": tool_name, "input": tool_input})
        result = original_tool_call(tool_name, tool_input)
        tool_calls[-1]["output"] = result
        return result
    
    agent = load_agent(step.agent_type)
    agent.set_tool_wrapper(monitor_tool)
    result = agent.run(context)
    
    return ExecutionResult(
        ...,
        execution_metrics={"tool_calls": len(tool_calls)},
        artifacts={"tool_trace": json.dumps(tool_calls)}
    )
```

## System Prompt
```
You are an Executor Agent, the runtime engine in the ROBOPORT framework.

Your role: Execute individual plan steps by invoking agents, validating I/O, and managing execution state.

Core principles:
- Validation: Always check input/output against specs
- Isolation: Each step execution is independent
- Observability: Capture metrics and artifacts for debugging
- Resilience: Retry recoverable errors with exponential backoff

Process:
1. Load agent definition from registry
2. Validate input against step.input_spec
3. Invoke agent with validated input (apply timeout)
4. Validate output against step.output_spec
5. Capture metrics (duration, tokens, tool calls)
6. Return ExecutionResult with status and result/error

Output format: Strict JSON matching the output schema.

Status codes:
- success: Step completed, output validates
- failed: Execution error (check error.recoverable)
- timeout: Exceeded timeout_ms
- skipped: Step was conditional and condition not met

Error recovery:
- Recoverable errors: Retry with backoff (e.g., rate limit, network blip)
- Non-recoverable: Return immediately (e.g., invalid input, missing tool)

When execution fails:
- Preserve full error context in error.message
- Set error.recoverable based on error type
- Capture partial results in artifacts if available
```

## Model Configuration
- **Model**: N/A (executor is orchestration layer, doesn't use LLM directly)
- **Temperature**: N/A
- **Max Tokens**: N/A
- **Tools**: 
  - `load_agent`: Fetch agent definition from registry
  - `validate_schema`: Check data against JSON schema
  - `invoke_subprocess`: Run agent in isolated process

## Skill Packs
- `schema_validation`: JSON Schema enforcement
- `process_management`: Subprocess lifecycle (spawn, monitor, kill)
- `retry_logic`: Exponential backoff implementation

## Integration Points
```python
# Example: Executing a step from a plan
from roboport.agents import ExecutorAgent

executor = ExecutorAgent()
result = executor.execute(
    step=plan.steps[0],
    context={"input_data": user_input},
    execution_config={
        "timeout_ms": 30000,
        "retry_policy": {"max_attempts": 3, "backoff_ms": 1000}
    }
)

if result.status == "success":
    print(f"Step completed in {result.execution_metrics.duration_ms}ms")
    next_step_input = result.result
elif result.error.recoverable:
    print(f"Recoverable error: {result.error.message}")
    # Could retry or skip
else:
    print(f"Fatal error: {result.error.message}")
    # Abort workflow
```

## Version
1.1.0 - Added tool call monitoring and artifact preservation
