# Evaluation Pipeline

## Purpose
Systematic evaluation framework for testing agent performance across multiple dimensions.

## Pipeline Stages

### 1. Test Generation
```python
# Create or load evaluation config
eval_config = {
    "eval_id": "code_gen_python",
    "prompts": [
        "Write a function to calculate Fibonacci numbers",
        "Create a REST API endpoint for user login",
        "Implement binary search algorithm"
    ],
    "criteria": [
        {"name": "correctness", "weight": 0.5, "scoring_guide": "..."},
        {"name": "readability", "weight": 0.3, "scoring_guide": "..."},
        {"name": "efficiency", "weight": 0.2, "scoring_guide": "..."}
    ],
    "pass_threshold": 75
}
```

### 2. Execution
```python
from roboport.eval import run_evaluation

results = run_evaluation(
    agent_id="code_generator_v1",
    eval_config=eval_config,
    num_runs=10  # Run each prompt 10 times for variance
)
```

### 3. Grading
```python
from roboport.agents import GraderAgent

grader = GraderAgent()
grades = []

for result in results:
    grade = grader.evaluate(
        output=result.output,
        criteria=eval_config["criteria"],
        context={"prompt": result.prompt}
    )
    grades.append(grade)
```

### 4. Analysis
```python
from roboport.agents import AnalyzerAgent

analyzer = AnalyzerAgent()
analysis = analyzer.analyze(
    evaluation_results=grades,
    analysis_type="failure_modes"
)

print(f"Mean score: {analysis.summary.mean_score}")
print(f"Pass rate: {analysis.summary.pass_rate*100}%")
for rec in analysis.recommendations:
    print(f"  [{rec.priority}] {rec.action}")
```

### 5. Iteration
```python
# Apply top recommendations
if analysis.recommendations:
    top_rec = analysis.recommendations[0]
    # Update agent config based on recommendation
    # Re-run evaluation
```

## Output Artifacts

```
evals/
└── code_gen_python/
    ├── config.json              # Evaluation definition
    ├── results_v1.jsonl         # Raw outputs
    ├── grades_v1.json           # Grading results
    ├── analysis_v1.json         # Analyzer output
    └── report_v1.md             # Human-readable summary
```

## Benchmarking

### Variance Analysis
```python
# Run same eval 10 times to measure consistency
variance_results = []
for i in range(10):
    result = run_evaluation(agent_id, eval_config)
    variance_results.append(result)

mean_score = np.mean([r.mean_score for r in variance_results])
std_dev = np.std([r.mean_score for r in variance_results])

print(f"Mean: {mean_score:.1f} ± {std_dev:.1f}")
```

### A/B Comparison
```python
from roboport.agents import ComparatorAgent

comparator = ComparatorAgent()
comparison = comparator.compare(
    candidates=[
        {"id": "gpt-4o", "output": results_gpt4},
        {"id": "claude-sonnet", "output": results_claude},
        {"id": "qwen3", "output": results_qwen}
    ],
    comparison_axes=["accuracy", "speed", "cost"]
)

print(f"Winner: {comparison.winner.overall}")
```

## Version
1.0.0 - Initial evaluation pipeline framework
