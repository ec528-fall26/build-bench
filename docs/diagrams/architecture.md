# Proposed repair agent architecture

All components are proposed. In-loop build feedback depends on the supported interface. Final clean-build validation and artifact checks are always required for verified success. The presentation diagram summarizes the flow; the Mermaid diagram below shows the tool/evidence loop and final organizer validation explicitly.

```mermaid
flowchart TD
    C[Case Manager: metadata, source, initial failure] --> E[Evidence Manager: log slices, files, build context]
    E --> P[LLM Planner: hypothesis and next action]
    P --> T[Controlled Tools: search and inspect]
    T --> E
    P --> R[Repair Generator: permitted worktree edits]
    R --> V[Validation/Feedback Adapter: supported in-loop feedback]
    V -->|failure evidence, when available| E
    V -->|feedback pass or budget stop| F[Final Worktree and completion output]
    F --> O[Organizer: mandatory canonical patch, clean build and artifact checks]
    H[History/Metrics: hypotheses, diffs and outcomes] -.-> P
    P -.-> H
    V -.-> H
    B[Budget Controls: tokens, time, builds and repeats] -.-> P
    B -.-> V
```

[Presentation diagram PDF](architecture.pdf) · [Editable Mermaid source](architecture.mmd) · [Design proposal](../design-proposal.md)
