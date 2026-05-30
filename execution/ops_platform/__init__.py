"""AI Operations Platform — extension of the existing project-architect platform.

This package adds:
- plugin_loader            scans /plugins, validates manifests
- capability_registry      in-memory registry + lookup + filtering
- response_contract        validates LLM responses against the contract
- workflow_runner          executes a workflow plugin end-to-end
- verification_agent       reviews a run, returns structured verification
- training_agent           generates a step-by-step walkthrough
- feedback_store           ratings + operational notes + suggested enhancements
- search_index             in-memory keyword + tag search across capabilities
- requirements_intelligence  extracts reusable patterns into requirements_writer

Everything here is additive — none of the existing project-architect /
advisory modules are modified. The ops platform consumes execution/llm_client,
state_manager (for project lookups), and requirements_writer (to feed
intelligence back into the requirements generation pipeline).
"""
