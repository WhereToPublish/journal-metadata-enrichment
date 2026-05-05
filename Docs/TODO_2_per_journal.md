The file README.md contains the project overview, and gives the context for the implementation of this project.
The file DONE.md contains what has already been done.
Read them in depth to understand the project, what is done and how it is implemented.

In this session, we will work on the following task:

* The script run_agent.sh should should trigger the whole pipeline, I should not have to copy-paste the command.
* In fact, remove the Webchat altogether, I should be able to monitor what the AI agent is doing while it is doing it, and I want to have the full log.
* So far the AI agent does not work, it does not produce the list of suggestions. Investigate and fix what is happenning.

Also, because of the context size of the LLM, it is best if the journal are processed one after the over, and that there is a reset of context between each journal. We should trigger one OpenClaw session per journal, that does the following:

* The .md files necesssary for OpenClaw (AGENTS.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md, USER.md, SKILL.md, ...) are not adapted for this per journal processing, adapt them.
* The OpenClaw session per journal should be the one using tools and browsing the web, ...
* Related to the previous point, the python scripts should be lightweight, they should parse the data from WhereToPublish, and do the loop over each journal that are going to be processed, but filling and browsing should be left to OpenClaw.
* In many places, absolute path are used, remove this and use relative paths (from the working directory where run_agent.sh is located)
* Use the .venv that is in the working directory and create a requirements.txt in the working directory
* Remove anything related to add new journals to the database for the AI agent (in the .md files of ./agent/ folder), it is out of scope now as we are parsing each journal.

Always run the pipeline, the AI agent and the code (not in dry-rn) to check that it is working. Don't finish until it is working as intended.
Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
