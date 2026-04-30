# **Architectural Design and Technical Audit of Agentic Metadata Maintenance for WhereToPublish**

The project WhereToPublish functions as a critical infrastructure for researchers in biology subfields, aiming to democratize access to publishing venues by aggregating fragmented metadata regarding Open Access (OA) status, Article Processing Charges (APC), and journal impact metrics. The current technical architecture, while functional, relies on a precarious balance of manual human curation and deterministic data processing scripts—specifically update\_extracted.py and data\_process.py. The proposal to integrate an autonomous AI agent to manage, update, and verify this data represents a transition from a static pipeline to a dynamic, probabilistic model. This transition is fraught with technical risks, hardware limitations, and logical inconsistencies that must be addressed with extreme rigor to ensure the scientific integrity of the resulting database.

## **Technical Analysis of the Current Data Pipeline**

The existing WhereToPublish infrastructure utilizes a GitHub-hosted repository that serves a static website generated from processed academic metadata. The data flow begins with human-curated Google Sheets, which act as the primary ingestion point. These sheets are categorized by scientific discipline and contain fields such as Journal Title, ISSN, and Publisher. The current pipeline's reliance on manual entry introduces a high probability of "silent failures"—errors such as transposed ISSN digits or inconsistent naming conventions that do not break the code but result in failed data joins during the enrichment phase.  
The transformation of raw input into the final web-facing dataset involves two primary scripts. The first, update\_extracted.py, manages the synchronization between the Google Sheets environment and the local data store. The second, data\_process.py, performs the heavy lifting of data normalization and enrichment. This script attempts to cross-reference entries against established academic databases such as Scimago, the Directory of Open Access Journals (DOAJ), and OpenAPC. The fundamental logic of this process is deterministic; it relies on exact or fuzzy matches of strings (journal names) and identifiers (ISSNs).

| Pipeline Component | Data Dependency | Technical Logic | Primary Failure Mode |
| :---- | :---- | :---- | :---- |
| Google Sheets | Human Input | Manual Curation | Typographical errors; outdated information |
| update\_extracted.py | Google Sheets API | Deterministic Sync | API rate limiting; authentication expiry |
| data\_process.py | Scimago, DOAJ, OpenAPC | String Matching / Joins | Join failures due to name variations |
| Website Deployment | Jekyll/GitHub Pages | Static Site Generation | Stale data if pipeline is not triggered |

The proposal to insert an AI agent into this flow seeks to address the "missing data" problem, where journals present in the Google Sheets are not enriched by external sources because of minor metadata discrepancies or because they are not yet indexed by the major aggregators.

## **Logical Audit of the AI Agent Integration Plan**

The intention to use an AI agent to "complement" rather than "replace" the pipeline is logically sound from a risk-management perspective, but it introduces several contradictions that must be resolved. The requirement for the agent to find "missing or incorrect information" assumes that the agent has a higher authority of truth than the current sources. This is a fallacy unless the agent is equipped with a hierarchy of source prioritization.

### **The Conflict of Authority**

The plan stipulates that the agent should cross-check information found on the internet. However, academic metadata is often contradictory. For example, a journal's official website may state a specific APC, while an aggregator like OpenAPC may report a different average price based on historical institutional payments. If the agent finds multiple sources confirming the same (potentially outdated) information, it may "confidently" suggest an error. The reasoning logic must be weighted: official publisher websites must hold the highest authority for APC and OA status, followed by indexing services like DOAJ, and finally secondary aggregators.

### **The Problem of Recursive Processing**

The plan suggests the agent should run the same scripts (update\_extracted.py and data\_process.py) as the current pipeline to identify gaps. While this is efficient for identifying which journals failed to join, it creates a potential feedback loop. If the agent "fixes" a journal name in its local environment to satisfy data\_process.py, but that fix is not mirrored in the Google Sheet exactly, the subsequent official run of the pipeline will fail again. The agent must prioritize identifying the *reason* for the failure (e.g., "The Scimago database uses the name 'Journal of Bio-Science' while the sheet uses 'Journal of Bioscience'") and suggest the specific amendment to the source of truth.

### **Disambiguation and the High-Confidence Requirement**

The requirement for "high confidence" and "multiple sources" is statistically problematic in the context of new or niche journals. For a newly launched journal, the publisher's website may be the *only* source of information. Requiring multiple sources would lead to the agent ignoring the most important new entries. The logic must be adjusted to assess source *quality* rather than source *quantity*.

| Verification Level | Requirement | Logic | Confidence Score |
| :---- | :---- | :---- | :---- |
| L1: Direct Match | ISSN found on Publisher Site | String equality check | 0.95 |
| L2: Aggregator Sync | DOAJ/Scimago match ISSN | Database cross-reference | 0.85 |
| L3: Heuristic Match | Name variation \+ Publisher match | Fuzzy string \+ Domain check | 0.70 |
| L4: New Discovery | Multiple news mentions | Search result aggregation | 0.50 |

## **Hardware Feasibility on Apple Silicon**

Running an agentic system locally on a MacBook Pro M2 with 32GB of RAM presents specific computational constraints. Agentic tasks, particularly those involving web browsing and simultaneous data processing, are memory-intensive.

### **LLM Selection and Quantization**

The 32GB RAM limit necessitates a careful choice of Large Language Model (LLM). A model like Llama-3-70B, even at 4-bit quantization, would consume approximately 40GB of VRAM, leading to heavy swapping and unsustainable performance on an M2 chip. Conversely, an 8B parameter model may lack the nuanced reasoning required to distinguish between complex academic licenses (e.g., CC-BY vs. CC-BY-NC-ND).  
The optimal choice for this hardware is a 14B or 27B parameter model, such as Qwen-2.5-32B (highly quantized) or Gemma-2-27B. These models offer the necessary reasoning depth for "agentic" loops—where the model must plan a search strategy, evaluate search results, and then formulate a suggestion—while remaining within the 32GB memory envelope.

### **Local Infrastructure Requirements**

To run an agent overnight, the system requires an inference engine that supports long-context management and tool calling.

1. **Ollama or LM Studio**: Can serve as the back-end for the local LLM, providing an OpenAI-compatible API.  
2. **Apple MLX Framework**: Specifically optimized for Apple Silicon, allowing for faster inference and better memory management than standard CUDA-based implementations.  
3. **Local State Management**: Since the agent will run overnight, it must maintain a persistent state (using a local SQLite database or JSON logs) so that if the process crashes or the network fluctuates, it can resume without re-browsing the same journals.

## **Architectural Strategy for Agentic Data Maintenance**

The use of OpenClaw as a foundation is viable, but its general-purpose nature requires significant modification for the academic domain. OpenClaw utilizes browser automation (Playwright/Puppeteer) to interact with the web, but academic publisher sites often employ aggressive anti-bot measures (e.g., Cloudflare Turnstile). A more robust strategy involves a multi-modal approach combining direct API access with targeted scraping.

### **The Search and Extraction Toolset**

The agent requires specialized "skills" or tools to function within the WhereToPublish ecosystem:

* **Identifier Resolver**: A tool that queries the Crossref API using a journal name to find the canonical ISSN-L.  
* **Academic Scraper**: A headless browser specifically configured with stealth plugins to bypass anti-bot measures on publisher domains.  
* **Sheet Connector**: A wrapper for the Google Sheets API (v4) to read data and write suggestions as comments or new columns.  
* **Pipeline Simulator**: A local environment where the agent can execute modified versions of data\_process.py to test if a suggested metadata change results in a successful data join.

### **Integration with Google Sheets API**

The proposed mechanism of making "suggestions" should be implemented via "Shadow Columns" or "Comments" to prevent the agent from corrupting the primary data without human oversight.

| Sheet Operation | Mechanism | Human Oversight |
| :---- | :---- | :---- |
| Read | spreadsheets.values.get | Passive |
| Suggest | spreadsheets.batchUpdate (Add Note) | Active Review |
| Metadata Add | Create new column AI\_Suggested\_OA | Active Review |
| Confidence Flag | Conditional Formatting (Red/Yellow/Green) | Passive |

The agent should use the notes field in Google Sheets to explain its reasoning. For example, "Changing journal name from 'Journal of Bio' to 'Journal of Biological Sciences' allows for a successful join with the Scimago database. Source: [https://www.scimagoir.com/journalsearch.php?q=](https://www.scimagoir.com/journalsearch.php?q)...".

## **Detailed Implementation Roadmap**

The implementation of this system must be incremental to ensure that the AI's suggestions do not overwhelm the human team.

### **Phase 1: Local Environment Setup and Model Calibration**

Before any automation occurs, the local environment on the MacBook Pro M2 must be optimized. This involves installing the necessary Python dependencies and configuring the LLM.

1. **Environment Isolation**: Create a dedicated Conda or Virtualenv for the agent to avoid conflicts with the existing WhereToPublish pipeline dependencies.  
2. **Quantized Model Loading**: Deploy a Qwen-2.5-14B-Instruct model using GGUF format via Ollama. This model size provides a balance between the speed needed for web searching and the logic needed for data verification.  
3. **Stealth Browser Configuration**: Setup Playwright with playwright-stealth to ensure the agent can access publisher pages without being immediately blocked.

### **Phase 2: Gap Identification and Prioritization**

The agent must not browse the internet aimlessly. Its first task is to identify where the current pipeline failed.

1. **Local Pipeline Execution**: The agent triggers update\_extracted.py to download the current Google Sheets.  
2. **Analysis of data\_process.py Logs**: The agent parses the logs to find entries where the join with DOAJ or Scimago failed.  
3. **Queue Generation**: The agent creates a list of "High Priority Gaps," such as journals that are missing an OA status or journals that failed the name-matching algorithm.

### **Phase 3: Agentic Research Loop**

This is the core of the overnight operation. The agent iterates through the priority queue.

1. **Search Phase**: For a given journal (e.g., "Biology Letters"), the agent uses a search tool (Tavily or Serper) to find the official publisher page and its entry in independent registries.  
2. **Extraction Phase**: The agent visits the publisher page and looks for metadata tags or specific text indicating the Article Processing Charge (APC) and the license type (e.g., CC-BY).  
3. **Cross-Checking Phase**: The agent compares the found data with the existing (incomplete) entry in the Google Sheet. It verifies that the ISSN found on the web matches the ISSN in the sheet.  
4. **Confidence Scoring**: The agent calculates a confidence score based on the authority of the sources and the consistency of the data across those sources.

### **Phase 4: Suggestion Submission and Explanation**

The agent completes its overnight run by updating the Google Sheets with its findings.

1. **Non-Destructive Updates**: The agent uses the Google Sheets API to populate a "Suggestions" column. It must never overwrite the "Journal Name" or "ISSN" columns directly.  
2. **Evidence Linking**: For every suggestion, the agent adds a cell note containing the URL of the source and a brief explanation of the logic (e.g., "Publisher site confirms transition to Gold Open Access as of Jan 2024").  
3. **Summary Report**: The agent generates a local Markdown report summarizing its findings, the number of gaps filled, and the journals it could not verify.

## **Potential Challenges and Mitigation Strategies**

The implementation of an agentic system is not without significant hurdles, particularly in the academic domain where precision is paramount.

### **Handling "Vague" or Ambiguous Journal Names**

The user's existing sheets contain journal names that may be ambiguous (e.g., "Cell"). An AI agent may find dozens of journals starting with "Cell."

* **Mitigation**: The agent must be instructed to never suggest an update based solely on a name match. It must find a matching ISSN or DOI prefix to confirm the journal's identity. If it cannot disambiguate, it must flag the entry as "Ambiguous \- Human Intervention Required" rather than making a low-confidence suggestion.

### **Overcoming Anti-Bot and Dynamic Content**

Modern publisher websites (e.g., Nature, ScienceDirect) are highly dynamic and use JavaScript extensively.

* **Mitigation**: Simple HTML parsing will fail. The agent must use a full-headed or headless browser that can execute JavaScript and wait for the page to settle before extracting data. Using the local M2's resources for this is feasible, but the agent must be limited to a small number of concurrent browser instances (e.g., 2-3) to avoid memory exhaustion.

### **Managing the "Nightly" Execution Constraint**

Running an agent overnight introduces the risk of state loss if the system enters sleep mode or the network disconnects.

* **Mitigation**: The implementation must include a "Checkpointing" system. After every 10 journals processed, the agent should save its current state to a local checkpoint.json file. Upon restart, it reads this file to determine where to resume. The computer's energy settings must be configured to prevent sleep while the agent script is active (e.g., using the caffeinate command on macOS).

## **Refined Configuration for OpenClaw**

To adapt OpenClaw for this specific task on the M2 hardware, the following configuration adjustments are required:

1. **LLM Provider**: Set to local with the endpoint pointing to the Ollama server (http://localhost:11434/v1).  
2. **Search Depth**: Configure as advanced to ensure the agent looks past the first page of search results for academic registries.  
3. **Max Iterations**: Limit to 15 iterations per journal to prevent the agent from getting stuck in "hallucination loops" where it repeatedly tries to find data that does not exist.  
4. **Tool Access**: Provide the agent with access to the local file system (read-only) for the WhereToPublish.github.io directory so it can reference the project's own logic.

## **Future Outlook and Scalability**

While the immediate goal is an experiment with a small subset of data (e.g., a single biology subfield sheet), the architecture is designed for horizontal scaling. Once the confidence of the agent's suggestions reaches a stable threshold, the system can be expanded to all 40+ sheets in the project.  
The ultimate iteration of this system would involve a semi-automated "Ground Truth" engine. When a human curator adds a new journal title to a sheet, the agent could automatically trigger a "Discovery Task" to find the corresponding ISSN, OA status, and publisher, effectively pre-populating the metadata for the human to verify. This flips the workflow from "Search and Entry" to "Review and Confirm," significantly increasing the throughput of the WhereToPublish project.  
The success of this agentic integration depends not on the sophistication of the LLM alone, but on the rigor of the verification logic and the seamlessness of the human-in-the-loop review interface. The MacBook Pro M2 provides a robust platform for this "metadata laboratory," enabling high-level research and data engineering to occur without the costs and privacy concerns of cloud-based AI providers.

## **Critical Critique of the "Suggestion" Interface**

The user suggested a "web interface that shows the suggestions and allows the team members to accept or reject them." While a dedicated interface is ideal, it adds another layer of technical debt. A more efficient approach for a small team is to leverage Google Sheets' native "Checkbox" and "Scripting" capabilities.

| Interface Element | Implementation | Advantage |
| :---- | :---- | :---- |
| **Suggestion Cell** | Column AI\_Value | Direct comparison |
| **Approval Cell** | Column Approve? (Checkbox) | Simple UX |
| **Logic/Source** | Cell Note | Contextual information |
| **Sync Script** | Google Apps Script (GAS) | One-click update of master data |

By using Google Apps Script, the team can create a custom menu in Google Sheets (e.g., "WhereToPublish \-\> Sync AI Suggestions"). When run, this script iterates through the rows, finds checked boxes, moves the AI\_Value to the Master\_Value column, and clears the AI suggestion. This minimizes the need for external web hosting and keeps the data ecosystem centralized within the Google Sheets environment.

## **Conclusion on Implementation Viability**

The proposed plan to use an AI agent to maintain the WhereToPublish database is a necessary evolution for the project. The current manual process cannot scale with the rapid growth of Open Access publishing. The MacBook Pro M2 is more than capable of hosting the required infrastructure, provided the agent is designed as a "Metadata Specialist" rather than a general-purpose assistant. The primary focus must remain on the disambiguation of journal entities and the hierarchical verification of data sources to maintain the academic standard of the repository. By following the incremental roadmap of gap identification, targeted research, and non-destructive suggestions, the project can safely integrate AI-driven efficiency without compromising its role as a trusted resource for the scientific community.  
The integration of the AI agent should be seen as a "force multiplier" for the human team. By automating the tedious task of searching for OA licenses and other fields  across fragmented publisher websites, the agent allows the human curators to focus on the high-level task of scientific subfield categorization and strategic data expansion. The logical flaws identified—such as source conflict and recursive processing—are manageable through explicit prompt engineering and a robust hierarchy of authority in the agent's reasoning engine. The transition to this agentic model will transform WhereToPublish from a manually updated directory into a near-real-time reflection of the academic publishing landscape.