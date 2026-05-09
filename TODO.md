The file README.md contains the project overview, and gives the context for the implementation of this project.
The file DONE.md contains what has already been done.
Read them in depth to understand the project, what is done and how it is implemented.

In this session, we will work on the following task:

- Observe the logs in the ./agent/output folder to identify how to improve the performance of the AI agent. Analyze the logs to understand when the AI agent made a wrong decision (both when proposing incorrect values, or when not suggestion the correct one), or when the AI agent did not manage to obtain it. Look for patterns in the logs that could indicate why the AI agent is not performing well, and try to understand the underlying causes of these issues. Based on this analysis, implement improvements by either changing the behavior of the OpenClaw agent (AGENTS.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md, USER.md, SKILL.md, ...). Test these improvements to see if they lead to better performance.
- For filling gap of ISSN, the AI agent should try to find one (or more) valid ISSN for the journal, but if the journal already has one ISSN (e-ISSN, p-ISSN, or ISSN-L), this should not be considered as a gap to fill. Adapt the AI agent to do this.
- Filling out "Alternative journal name" such that the join with Scimago (H index, SJR, quartile) or DOAJ is working better is a very high priority gap to fill, as it will allow us to have more information about the journal. Adapt the AI agent to prioritize filling this gap (only when it didn't merge with Scimago or DOAJ). Also, if the journal contains an ISSN, that can be used to find the alternative journal name in the different data sources, so the AI agent should use this information to fill the "Alternative journal name" column.
- For the business model, the AI agent should not guess just by looking at the publisher and making guess such as "The publisher is Wolters Kluwer (Lippincott), which is known for subscription-based models.", find hard evidence of the business model of the journal by looking at the journal website, or other sources, and only then fill the business model. Adapt the AI agent to do this.

Implement these changes, always run the pipeline, the AI agent and the code (not in dry-run) to check that it is working. Don't finish until it is working as intended.
Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
