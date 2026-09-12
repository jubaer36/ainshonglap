**ENTRY ASSESSMENT** 

**Criminal Law Q\&A Assistant** 

Design and build a source-grounded legal information assistant for Bangladesh. 

**ASSESSMENT PRIORITY** 

**Development process, experiments, documentation, and meaningful Git history are crucial.** 

**01 OBJECTIVE** 

Build a web-based Q\&A assistant for: 

∙ The Code of Criminal Procedure, 1898 (Act No. V of 1898\) 

∙ Relevant amendments and related laws of Bangladesh 

The assistant should help users understand legal provisions through accurate, source-grounded answers with clear citations. 

**02 CORE REQUIREMENTS** 

Develop a functional web application with: 

∙ Text-based question input 

∙ Backend API 

∙ LLM integration using a self-hosted model, vLLM, or free/paid API 

∙ Legal-document ingestion and retrieval 

∙ Section-level citations and relevant source excerpts 

∙ Proper handling of ambiguous and unsupported questions 

∙ A clear disclaimer that the system provides legal information, not legal advice 

**Optional features:** 

∙ Image input 

∙ Speech input 

∙ PDF or document upload 

**03 LEGAL-DOCUMENT PROCESSING** 

The solution should draw on reliable legal sources, with parsing that respects section and subsection boundaries and preserves document hierarchy and metadata. Chunking and retrieval strategy, handling of amendments and document versions, and the ability to add or replace legal documents are all worth thinking through carefully, with traceable citations running throughout. Design choices are best documented as they're made, along with the reasoning for revisiting or changing course — grounded in what the data actually shows rather than assumption. 

**04 DEVELOPMENT AND EXPERIMENTS** 

The solution should be developed incrementally, moving through stages such as an LLM-only baseline, basic vector retrieval, legal-aware chunking, hybrid retrieval or reranking, prompt improvements, citation validation, and hallucination reduction. For the experiments that matter most, it's worth capturing the objective and hypothesis behind each one, the configuration used, the results observed, any failure cases encountered, and the decision made along with the next step.

**PAGE** 1   
**05 EVALUATION** 

Evaluation tends to be most informative when it isn't just a single measurement taken at the end. Revisiting the dataset after changes and noticing how results shift often reveals more than a one-time score. A small number of carefully examined failure cases, along with what was tried next, can say more about a system than aggregate metrics alone — and often, the reasoning behind a change in approach becomes clearest when traced back to the result that prompted it. 

**06 AGENTIC CODING TOOLS** 

Candidates are fully permitted to use agentic coding tools, AI coding assistants, code-generation and debugging tools, and agents capable of creating code, commits, branches, or pull requests — we're fully open to modern AI-native software-development workflows. That said, all submitted work must be reviewed, tested, and understood well enough to be explained in detail, and an AI\_USAGE.md file should be included describing the tools used, the tasks delegated to them, the review process applied, and any AI-generated suggestions that were rejected or corrected. 

**07 DOCUMENTATION** 

Include a clear README.md covering: 

∙ Architecture and technology choices 

∙ Setup and run instructions 

∙ Data ingestion and update process 

∙ Evaluation process 

∙ Experiments and results 

∙ Limitations and future improvements 

**08 DELIVERABLES** 

∙ Git repository URL 

∙ Functional web application and API 

∙ Legal-document ingestion and retrieval pipeline 

∙ Evaluation dataset and results 

∙ Experiment and failure-analysis report 

∙ Incremental document-update demonstration 

∙ Documentation and architecture diagram 

∙ Meaningful Git history 

∙ Demo video or deployment link 

∙ AI\_USAGE.md

**PAGE** 2 