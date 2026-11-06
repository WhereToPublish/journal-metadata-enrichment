This is to work on "journal-metadata-enrichment", inside of the WhereToPublish project

The file "journal-metadata-enrichment/README.md" contains the project overview, and gives the context for the implementation of this project.
The file "journal-metadata-enrichment/DONE.md" contains what has already been done.
The file "./README.md" contains the WhereToPublish project overview.

Read them in depth to understand the project, the dependencirs, what is done and how it is implemented.

In this session, we will work on the following task:

- Implement this kind of AI agent into Mistral Studio (I have a pro subscription). MistralStudio.md file gives the overall process, feasibility and how would that work, what would need to change, the step-by-step instruction, and the advantage/drawback compared to the current OMP implementation. Implement this while being careful with small test first.
- Use Pydantic instead of JSONL-parsing + comment-stripping + retry logic in runtime.
- Once it words, update the MistralStudio.md to explain how the implementation, and what will be necessary to do next to have a full Mistral Studio orchestrator running from there.
- The API_KEY is already set.

Always run the pipeline, the AI agent and the code (not in dry-rn) to check that it is working. Don't finish until it is working as intended.

Don't touch scripts in "WhereToPublish" (unless absolutely necessity), edit what is inside "journal-metadata-enrichment".

Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
