The file README.md contains the project overview, and gives the context for the implementation of this project.
The file DONE.md contains what has already been done.
Read them in depth to understand the project, what is done and how it is implemented.

In this session, we will work on the following task:

- When uploading suggestions to the google sheet ("Agent_suggestions" sheet), the first column is whether we approve or not the suggestion (it is boolean): it should be a column with a dropdown menu with "pending", "approve", "reject", "maybe" as options. Suggestions should be uploaded with the "pending" status, then the team members can review them and change the status to "approve", "reject". This way, we can keep track of the suggestions and their review status in a more structured way. Adapt the script in GOOGLE_API_SCRIPT.md to do this, and adapt the AI agent to fill the status column with "pending" when it is making suggestions.
- When suggestions are parsed in the google sheet by Google App script, the "pending" suggestions should not be touched, and the "approve" suggestions should be used to update the data. Also, when the script runs, both the "approve" and "reject" suggestions should be added in the sheet "Agent_suggestions_processed" to keep track of them, and to be able to analyze them later on. Adapt the Google App script (in GOOGLE_API_SCRIPT.md) to do this.
- In the runtime of the AI agent, it should check that the suggestions is not already in the sheet "Agent_suggestions_processed" or in the sheet "Agent_suggestions" before making a suggestion, to avoid making duplicate suggestions. Adapt the AI agent to do this.
- In long terme, we want to use the history of suggestions and their review status to analyze the performance of the AI agent, and to improve it. For example, we can analyze the "approve" suggestions to see if they were correct or not, and to see if there are patterns in the suggestions that are more likely to be approved or rejected. We can also analyze the "reject" suggestions to see if there are patterns in the suggestions that are more likely to be rejected. Write in the NEXT_STEPS.md how we can use the history of suggestions and their review status to analyze the performance of the AI agent, and to improve it.

Implement these changes, always run the pipeline, the AI agent and the code (not in dry-rn) to check that it is working. Don't finish until it is working as intended.
Always refactor the code for unused functions, remove duplicate code and make sure the code is modular.
After implementation is working well and tested, before finishing, update the README.md and the DONE.md with the new state of the project.
The README.md and the DONE.md so must describes the state of the project after the implementation of the above tasks, but that it should not contain the change history (i.e. chansgelogs), but only the current state of the project.
