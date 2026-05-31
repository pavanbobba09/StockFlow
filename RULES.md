# RULES

Coding rules for this project. Read alongside CLAUDE.md.

## Keep it simple (KISS)

- Write the simplest code that solves the problem. No clever tricks.
- Don't build for problems you don't have yet. No premature abstraction.
- Prefer plain functions over layers of classes and patterns.
- If a solution feels complicated, it's probably wrong. Step back and simplify.
- Fewer moving parts beats a clever design. Easy to read, easy to change.

## Comments

- Keep comments minimal. Write them only when they're necessary.
- Good code explains itself. Name things well so comments aren't needed.
- Comment the why, not the what. The code already shows what it does.
- No comments that just repeat the code or state the obvious.
- Delete stale comments. A wrong comment is worse than none.

## Handoff before compacting the chat

Before the chat gets compacted or context runs low, write a file called
PROGRESS.md (overwrite the old one each time) that explains what has been built
so far. The next session reads it and picks up cleanly. Keep it in plain simple
terms, like explaining to someone new. It must cover:

- What's built so far and what still isn't. A quick checklist against the phases
  in docs/WORK_BREAKDOWN.md so it's clear where we are.
- Every function written: its name, what it does in one line, what it takes in,
  what it gives back.
- Every API call: which endpoint, what it's for, what goes in, what comes out,
  and who calls it (the frontend, the scheduler, an agent).
- How each agent works in plain words: what wakes it up, what it reads, what it
  decides, what tools it calls, and where its proposal goes.
- How the pieces talk to each other: the flow from a tick of the clock through
  the agent to a proposal to a human approving it to the state changing.
- Confirm it follows the architecture (docs/ARCHITECTURE.md) and these rules.
  Note anything that broke a rule and why.
- Anything half-done, broken, or about to change, so the next session is warned.

Write it so a person who has never seen the project understands what exists and
how it works after one read. No jargon dump. Explain it like you're handing the
project to a new teammate.
