"""Bounded compliance, pipeline and posting-date guards; substantive review remains required."""

import re
from datetime import date

PIPELINE = re.compile(r"\bdata[-\s]+pipelines?\b", re.IGNORECASE)
FUTURE = re.compile(r"\b(?:(not)\s+)?(?:future[- ]dated|(?:from|in)\s+(?:the\s+)?future|future (?:posting|year))\b", re.IGNORECASE)


def validate_recommendations(output, candidate, accumulated, inputs=None):
    # Shape errors remain the schema validator's responsibility. This guard
    # also participates in repair and must safely inspect malformed output.
    if not isinstance(output, dict):
        return []
    actions = output.get("recommended_actions", [])
    if not isinstance(actions, list):
        actions = []
    frameworks = {"HIPAA", "GDPR", "SOC2", "SOC 2"}
    for value in accumulated.values():
        if isinstance(value, dict):
            frameworks.update(value.get("frameworks_implied") or [])
    declared = {
        str(v).casefold()
        for key in ("skills", "certifications", "compliance_frameworks")
        for v in candidate.get(key, [])
        if isinstance(v, str)
    }
    issues = []
    pipeline_declared = any(PIPELINE.fullmatch(skill.strip()) for skill in candidate.get("skills", []) if isinstance(skill, str))
    for action in actions:
        if not isinstance(action, str):
            continue
        if PIPELINE.search(action) and not pipeline_declared:
            issues.append("Data-pipeline capability is not declared in candidate inputs. Do not recommend claiming it; discuss uncertainty separately.")
        for framework in frameworks:
            if framework.casefold() not in declared and re.search(
                r"(?<!\w)" + re.escape(framework) + r"(?!\w)", action, re.IGNORECASE
            ):
                issues.append(
                    "Recommendation names compliance capability absent from candidate inputs: "
                    + framework
                )
    if inputs is not None and "jobs" in inputs:
        context = inputs.get("evaluation_context")
        try:
            as_of = date.fromisoformat(context["as_of_date"])
            job = next(job for job in inputs["jobs"] if job["id"] == output.get("job_id"))
            posted = date.fromisoformat(job["posted_at"])
            if context.get("timezone") != "UTC":
                raise ValueError("expected frozen UTC date")
        except (KeyError, TypeError, ValueError, StopIteration):
            issues.append("Valid frozen evaluation date and matching supplied job posting date are required.")
        else:
            if posted <= as_of:
                for key in ("reasoning_for", "reasoning_against", "recommended_actions"):
                    values = output.get(key, [])
                    if not isinstance(values, list):
                        continue
                    if any(isinstance(value, str) and any(not m.group(1) for m in FUTURE.finditer(value)) for value in values):
                        issues.append(f"Posting {posted.isoformat()} is not future-dated relative to frozen evaluation date {as_of.isoformat()} UTC.")
                        break
    return issues
