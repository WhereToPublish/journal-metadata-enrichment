This is to work on "journal-metadata-enrichment", inside of the WhereToPublish project

The file "journal-metadata-enrichment/README.md" contains the project overview, and gives the context for the implementation of this project.
The file "journal-metadata-enrichment/DONE.md" contains what has already been done.
The file "./README.md" contains the WhereToPublish project overview.

Read them in depth to understand the project, the dependencirs, what is done and how it is implemented.

In this session, we will work on the following task:

- Update the project so that the path and everything works from inside of the "journal-metadata-enrichment" folder, which is now inside WhereToPublish folder.
- Don't use OpenClaw with Docker anymore, use OMP (oh-my-pi) as the agent, which is already installed and already is plugged to an LLM.

Always run the pipeline, the AI agent and the code (not in dry-rn) to check that it is working. Don't finish until it is working as intended.

Don't touch scripts in "WhereToPublish" (unless absolutely necessity), edit what is inside "journal-metadata-enrichment".
Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
