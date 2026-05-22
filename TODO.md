
The file README.md contains the project overview, and gives the context for the implementation of this project.
The file DONE.md contains what has already been done.
Read them in depth to understand the project, what is done and how it is implemented.

In this session, we will work on the following task:

- Observe the logs in the ./agent/output folder to identify how to improve the performance of the AI agent. Analyze the logs to understand when the AI agent made a wrong decision (both when proposing incorrect values, or when not suggestion the correct one), or when the AI agent did not managed to obtain. Look for patterns in the logs that could indicate why the AI agent is not performing well, and try to understand the underlying causes of these issues. Based on this analysis, implement improvements by either changing the behavior of the OpenClaw agent (AGENTS.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md, USER.md, SKILL.md, ...) or the pipeline. Test these improvements to see if they lead to better performance.

Always run the pipeline, the AI agent and the code (not in dry-rn) to check that it is working. Don't finish until it is working as intended.
Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
