# Prompts

One directory per task, one file per version: `<task>/v<N>.md`, loaded by `app/ai/prompt_registry.py`.

A file is markdown with two headings:

```markdown
# System

Instructions that do not change per call.

# User

Whatever frames the request, with {{input}} where the payload goes.
```

Rules:

- Never edit a shipped version in place. Add `v<N+1>.md` instead: the version is part of the
  response-cache key and is stored as `prompt_version` on every value the prompt produced, so
  editing one silently invalidates both the cache and the eval results.
- Treat crawled or user-supplied content as data, never as instructions (docs/07 grounding rules).
- Re-run the task's eval set before making a new version the default (docs/07 evals).
