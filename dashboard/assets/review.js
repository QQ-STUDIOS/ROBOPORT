/* Artifact presentation only. Every imported field remains inert text. */
(function (root) {
  "use strict";
  const names = {
    changes: "Proposed changes",
    evidence: "Evidence reference",
    ranked_matches: "Ranked matches",
    match: "Recommendation",
    compliance: "Compliance findings",
    reasoning_for: "Reasons to proceed",
    reasoning_against: "Concerns",
    expected_impact: "Expected impact",
    approval_required: "Review required",
    llm_calls: "Model calls",
    spent_usd: "API spend (USD)",
    reserved_usd: "Reserved API spend (USD)",
    elapsed_seconds: "Elapsed time (seconds)",
  };
  const substantiveKeys = new Set([
    "match",
    "compliance",
    "findings",
    "red_flags",
    "regulated_data",
    "frameworks_implied",
    "geographic_constraints",
    "limitations",
    "uncertainties",
    "warnings",
    "reasoning_for",
    "reasoning_against",
    "recommended_actions",
    "risks",
    "constraints",
    "error",
    "errors",
    "blocker_failed",
    "candidate_inputs",
    "job_inputs",
    "source_inventory",
    "source_samples",
    "samples_omitted",
    "scope",
  ]);
  function containsConcern(value, depth = 0) {
    if (value === null || typeof value !== "object") return false;
    // Deep unknown structures stay exposed rather than hiding an unexamined risk.
    if (depth >= 6) return true;
    return Object.entries(value).some(
      ([key, child]) =>
        (substantiveKeys.has(key) &&
          child !== null &&
          (Array.isArray(child)
            ? child.length > 0
            : typeof child === "object"
              ? Object.keys(child).length > 0
              : String(child).trim() !== "")) ||
        containsConcern(child, depth + 1),
    );
  }
  function label(key) {
    return Object.hasOwn(names, key)
      ? names[key]
      : String(key)
          .replaceAll("_", " ")
          .replace(/^./, (c) => c.toUpperCase());
  }
  function node(tag, text, cls) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  }
  function inline(parent, text) {
    for (const part of String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
      if (part.startsWith("**") && part.endsWith("**"))
        parent.append(node("strong", part.slice(2, -2)));
      else if (part.startsWith("`") && part.endsWith("`"))
        parent.append(node("code", part.slice(1, -1)));
      else parent.append(document.createTextNode(part));
    }
  }
  function text(parent, value) {
    if (typeof value !== "string") {
      parent.append(
        node(
          "span",
          value === null
            ? "Not provided"
            : typeof value === "boolean"
              ? value
                ? "Yes"
                : "No"
              : value,
        ),
      );
      return;
    }
    if (!value.trim()) {
      parent.append(node("span", "Not provided"));
      return;
    }
    let list = null,
      fenced = false,
      code = [];
    for (const line of value.split("\n")) {
      if (/^\s*```/.test(line)) {
        if (fenced) {
          parent.append(node("pre", code.join("\n")));
          code = [];
        }
        fenced = !fenced;
        list = null;
        continue;
      }
      if (fenced) {
        code.push(line);
        continue;
      }
      const heading = line.match(/^#{1,4}\s+(.+)$/),
        item = line.match(/^\s*(?:[-*]|\d+[.)])\s+(.+)$/);
      if (heading) {
        list = null;
        const h = node("h4");
        inline(h, heading[1]);
        parent.append(h);
      } else if (item) {
        const tag = /^\s*\d/.test(line) ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = node(tag);
          parent.append(list);
        }
        const li = node("li");
        inline(li, item[1]);
        list.append(li);
      } else {
        list = null;
        if (line.trim()) {
          const p = node("p");
          inline(p, line);
          parent.append(p);
        }
      }
    }
    if (fenced) parent.append(node("pre", code.join("\n")));
  }
  function fields(parent, value, depth = 0) {
    const facts = node("dl", undefined, "report-facts");
    for (const [key, child] of Object.entries(value)) {
      if (child === null || typeof child !== "object") {
        const dd = node("dd");
        text(dd, child);
        facts.append(node("dt", label(key)), dd);
      }
    }
    if (facts.childElementCount) parent.append(facts);
    for (const [key, child] of Object.entries(value)) {
      if (child === null || typeof child !== "object") continue;
      const technical = [
        "evidence",
        "job",
        "technical",
        "compliance",
        "component_hashes",
        "project_hashes",
        "model_digests",
      ].includes(key);
      const substantive = substantiveKeys.has(key) || containsConcern(child);
      const section = node(
        (technical || depth > 1) && !substantive ? "details" : "section",
        undefined,
        "report-section",
      );
      section.append(
        node(
          section.tagName === "DETAILS"
            ? "summary"
            : "h" + Math.min(6, depth + 3),
          label(key),
        ),
      );
      if (depth > 4)
        section.append(node("pre", JSON.stringify(child, null, 2)));
      else if (Array.isArray(child)) {
        if (!child.length) section.append(node("p", "None recorded"));
        else if (child.every((v) => v === null || typeof v !== "object")) {
          const ul = node("ul");
          for (const v of child) {
            const li = node("li");
            text(li, v);
            ul.append(li);
          }
          section.append(ul);
        } else {
          child.forEach((v, i) => {
            const record = node("article", undefined, "report-record");
            const title =
              v?.description || v?.job?.title || `${label(key)} ${i + 1}`;
            record.append(node("h" + Math.min(6, depth + 4), title));
            if (v !== null && typeof v === "object") {
              const rest = { ...v };
              if (v.description) delete rest.description;
              fields(record, rest, depth + 2);
            } else text(record, v);
            section.append(record);
          });
        }
      } else fields(section, child, depth + 1);
      parent.append(section);
    }
  }
  function render(parent, artifact) {
    const report = node("div", undefined, "readable-report");
    const content = Object.hasOwn(artifact, "content")
      ? artifact.content
      : artifact;
    if (
      content !== null &&
      typeof content === "object" &&
      !Array.isArray(content)
    )
      fields(report, content);
    else if (Array.isArray(content)) fields(report, { results: content });
    else text(report, content);
    parent.append(report);
    const raw = node("details", undefined, "raw-artifact");
    raw.append(
      node(
        "summary",
        typeof content === "string" ? "Original text" : "Raw JSON",
      ),
      node(
        "pre",
        typeof content === "string"
          ? content
          : JSON.stringify(content, null, 2),
      ),
    );
    parent.append(raw);
  }
  root.RoboportReview = { render };
})(window);
