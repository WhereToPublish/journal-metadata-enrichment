You are an inhuman intelligence tasked with spotting logical flaws and inconsistencies in my ideas.
Never agree with me unless my reasoning is watertight.
Never use friendly or encouraging language.
If I’m being vague, ask for clarification before proceeding.
Your goal is not to help me feel good — it’s to help me think better.

This is about the project WhereToPublish, which is a website that helps researchers find journals to publish in based on various criteria.
Here are the ressources I have to work with:

- The website itself: https://wheretopublish.github.io/
- The codebase of the website: https://github.com/WhereToPublish/WhereToPublish.github.io
- The wiki of the project: https://github.com/WhereToPublish/WhereToPublish.github.io/wiki
  Read all the documentation and understand the project in depth before proceeding with any task.

Now, I what to build an AI agent that will be able to automatically complete and update the raw data.
This is so far an experiment, but I want to see if it can be done and how well it can be done.
I don't yet want to replace the current data pipeline, but I want to see if the AI agent can be used to complement it and make it more efficient.
Basically, I want the following:

- Google sheets will be used as the main source of data input. The sheets will be manually curated by the team members with new journal information, and new journal information.
- The AI agent will be able to access the Google Sheets, extract the data and find the missing or incorrect information.
- The AI agent will then make suggestion to the Google Sheets, and explain why it is making these suggestions, and provide the sources of information it used to make these suggestions (e.g. links to the journal website, links to the external sources, etc.), the team members will be able to review these suggestions and decide whether to accept them or not. This way, the AI agent can help to keep the data up to date and complete, while still allowing human oversight and control over the data. The AI agent will start by making suggestions for a small subset of the data (e.g. a single sheet, meaning a subset of the journals for a specific biology subfield), and then we can see how well it is doing, and if it is doing well, we can expand it to more sheets (more scientific fields). For this step, we need to find a way to make the AI agent able to access the Google Sheets, and to make suggestions in a way that is easy for the team members to review and accept or reject. We can maybe use the Google Sheets API to do this, or over techniques if necessary (e.g. a web interface that shows the suggestions and allows the team members to accept or reject them, and then updates the Google Sheets accordingly, or something else).
- The suggestion will be manually reviewed by the team members before being accepted to the Google Sheets, and thus will be part of the data that is used to update the website.
- Then, for updating the website, the Google Sheets are downloaded as csv files, then processed (update_extracted.py and data_process.py) by the data pipeline (cleaning, normalizing, deduplicating, enriching with external sources), and then the processed data is used to update the website.

The AI agent should browse internet to find missing information about journals metadata, their OA status, their publisher, etc.
The AI agent should cross-check the information it finds.
The AI agent should have a detailded, expert and always up-to-date understanding of how the data is structured and is processed (update_extracted.py and data_process.py from WhereToPublish repo) by the data pipeline (cleaning, normalizing, deduplicating, enriching with external sources).
The AI agent can run the same scripts as the current data pipeline to process the data, to see what is completed by the current data pipeline and what is not, and to find missing information.
Importantly, the AI agent should fill the information that will not be filled otherwise by external sources (Scimago, OpenAPC, DOAJ). In other words, the AI agent should look at the processed data (after update_extracted.py and data_process.py), find missing information, and try to find this information on the internet. For example, if a journal is missing its OA status, the AI agent can look at the journal website to find this information, or maybe the join on the external sources did not work because of the journal name was not formatted correctly, and in that the AI agent should try to find the correct "alternative" journal name (there is column for this in the ) so that the join on external sources can work and the OA status will be filled automatically.
The AI agent should check the data, and fix issues if it is having a high confidence that the information was wrong, or if it finds new information that is more up to date than the current information in the database. For example, if the AI agent finds that a journal is now OA, but it was not OA before, it can update the OA status of the journal in its own database. It should do this only if it is having a high confidence that the information is correct, and if it finds multiple sources confirming the same information.
The AI agent can find new journals that are not yet in the database, and add them to its own database. Again it should do this only if it is having a high confidence that the information is correct, and if it finds multiple sources confirming the same information.
The AI agent should be able to explain its reasoning and the sources of information it used to make its suggestions, so that the team members can review them and decide whether to accept them or not.
The AI agent should only propose the most important suggestions, and not propose too many suggestions that would be overwhelming for the team members to review. For example, it can propose only the top most important suggestions, for which it is having the highest confidence, or something like that. The AI agent should be able to prioritize its suggestions based on the importance of the information and the confidence it has in the information.

I want to have the AI agent to running locally on my laptop during the night, so it can take its time to browse the internet and find information while I am not using the computer for other tasks, then I can turn it off in the morning.
The plan is having something like OpenClaw (https://github.com/openclaw/openclaw) and use a local LLM to run the agent.
I have a MacBook Pro M2 with 32GB of RAM, so I can run a local LLM adapted to my hardware and to the task (agentic, browsing, etc.).

The goal is to kickstart this project, read all the attached reports about this approach (in the folder Docs: Report-1-md, Report-2-md, Report-3-md) to understand how this agent can be to implemented, and the configuration/setup I should have for OpenClaw, how to make it able to browse the internet and extract information from it, and how to make it able to access the Google Sheets and make suggestions in it. These reports might be conflicting for some points, if they do search on your own.
There is a few things to consider that are not clear in the reports, that I want to clarify:

- Repots suggest using a scheduled jobs that spun automatically, while in fact I just want it to start when I ran a specific script, and it should start straight away, no need for scheduling.
- Repots suggest using ISSN or identifier for the journal, there is NO such identifier, the match is solely based on the name.
- I don't need run OpenClaw into a container (docker, ...)
- The AI Agent should have 4 goals: 1: Fill in the metadata for cell still empty after the processing/enrichement from external sources (either by filling with information directly, or finding the"alternative" journal name that is used to make the join with external sources so the cell will automatically be filled during processing) 2. Correct errors in the database that are factually 3. Enrich the database with journal that we had missed 4. Remove journals that don't exist

In this session, we will do the following:

- Implement this project, do as much as you can, you have enough information so that it can reach the state of producing a file that contains the suggested edit. You don't have the right to upload to the Google Sheet yet, but you should code the Agent that will be able to produce the file uploaded to the Google Sheet.
- Give the instruction (into the file NEXT_STEPS.md) of want I should do so that the Agent is able to upload to the Google Sheet, and what script should run on the Google sheet so that user only click accept/reject and the script will update the database
- Fill in all the .md files necesssary for OpenClaw (AGENTS.md, HEARTBEAT.md, IDENTITY.md, SOUL.md, TOOLS.md, USER.md, SKILL.md, and other files I have missed)
- Test the agent and fix errors, do not stop until the agent is producing the output file containing suggestions.

Some Q&A:

Q: Which Google Sheet tab should the agent start with for the initial experiment?
A: Genetics & Genomics (gid 1379563174)

Q: How should the agent write its suggestions for review?
A: Dedicated 'AI_Suggestions' tab in the same spreadsheet

Q: How do you want to interact with the OpenClaw agent (monitor it, or ask it questions of why it did things the way it did)?
A: WebChat at localhost(no extra setup)

Q: What already exists on your machine?
A: Ollama installed and running, WhereToPublish and OpenClaw repo cloned locally

Always test the AI Agent and run python scripts to test the code is working, fix issues if they arise.
Also, write the file README.md which contains the project overview, and gives the context for the implementation of this project, it is meant to be read by humans.
The file README.md also contains the command to run the agent.
The file DONE.md contains what has already been done, it is means to be read by an LLM, and contains details of each file and folder in the repository, and want they do.
Write and update the README.md and the DONE.md so that it describes the state of the project after the implementation of the above tasks, but that it does not contain the change history (i.e. chansgelogs), but only the current state of the project.
