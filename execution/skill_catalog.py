"""Skill catalog and suggestion engine for Skill Discovery.

Loads a global skill registry (config/skill_registry.json) containing
Claude-compatible skills/tools scraped from external sources.  Suggests
relevant skills for a project based on its profile and selected features.
Falls back to a hardcoded subset when the registry file is missing.
"""

import json
import logging
from pathlib import Path

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent / "config" / "skill_registry.json"

SKILL_CATEGORIES = [
    "MCP Servers",
    "AI Agent Frameworks",
    "LLM Tool Libraries",
    "Automation & Integration",
    "Data & RAG",
    "Code & Development",
    "Communication & Collaboration",
    "Monitoring & Observability",
    "Security & Auth",
    "Cloud & Infrastructure",
    "ML & Data Science",
    "Frontend & UI",
    "DevOps & Deployment",
    "Testing & QA",
    "Media & Content",
    "Custom Skills",
]

# Comprehensive fallback when registry file is missing (~200 high-value skills)
FALLBACK_SKILLS = [
    # ── Existing 50 skills ──────────────────────────────────────────────
    {"id": "web_search", "name": "Web Search", "description": "Search the internet for real-time information via Google, Bing, or Brave", "category": "Data & RAG", "tags": ["search", "web", "real-time"]},
    {"id": "rag_pipeline", "name": "RAG Pipeline", "description": "Retrieve relevant documents from vector stores to ground LLM responses", "category": "Data & RAG", "tags": ["rag", "retrieval", "vector-store"]},
    {"id": "sql_query_tool", "name": "SQL Database Query Tool", "description": "Translate natural language to SQL and query relational databases", "category": "Data & RAG", "tags": ["sql", "database", "query"]},
    {"id": "document_loader", "name": "Document Loader & Parser", "description": "Load and parse PDFs, Word docs, CSVs, and other file formats", "category": "Data & RAG", "tags": ["documents", "parsing", "pdf"]},
    {"id": "embedding_generator", "name": "Embedding Generator", "description": "Generate vector embeddings from text for semantic search", "category": "Data & RAG", "tags": ["embeddings", "vectors", "semantic-search"]},
    {"id": "web_scraper", "name": "Web Scraper & Data Extractor", "description": "Scrape websites and extract structured data from HTML pages", "category": "Data & RAG", "tags": ["scraping", "web", "extraction"]},
    {"id": "data_analytics", "name": "Data Analytics & Reporting", "description": "Generate analytics reports, charts, and insights from structured data", "category": "Data & RAG", "tags": ["analytics", "reporting", "charts"]},
    {"id": "mcp_filesystem", "name": "MCP Filesystem Server", "description": "Read, write, and manage local files via Model Context Protocol", "category": "MCP Servers", "tags": ["file-io", "mcp", "local"]},
    {"id": "mcp_github", "name": "MCP GitHub Server", "description": "Interact with GitHub repos, issues, PRs, and actions via MCP", "category": "MCP Servers", "tags": ["github", "mcp", "vcs"]},
    {"id": "mcp_slack", "name": "MCP Slack Server", "description": "Send messages, read channels, and manage Slack workspaces via MCP", "category": "MCP Servers", "tags": ["slack", "mcp", "messaging"]},
    {"id": "mcp_postgres", "name": "MCP PostgreSQL Server", "description": "Query and manage PostgreSQL databases via Model Context Protocol", "category": "MCP Servers", "tags": ["database", "mcp", "sql"]},
    {"id": "mcp_browser", "name": "MCP Browser Automation", "description": "Control web browsers for scraping, testing, and automation via MCP", "category": "MCP Servers", "tags": ["browser", "mcp", "automation"]},
    {"id": "mcp_memory", "name": "MCP Memory Server", "description": "Persistent knowledge graph memory for Claude conversations via MCP", "category": "MCP Servers", "tags": ["memory", "mcp", "knowledge-graph"]},
    {"id": "mcp_brave_search", "name": "MCP Brave Search Server", "description": "Web and local search capabilities via Brave Search API and MCP", "category": "MCP Servers", "tags": ["search", "mcp", "brave"]},
    {"id": "claude_tool_use", "name": "Claude Tool Use (Function Calling)", "description": "Define custom functions Claude can call to interact with external systems", "category": "LLM Tool Libraries", "tags": ["claude", "tool-use", "function-calling"]},
    {"id": "claude_computer_use", "name": "Claude Computer Use", "description": "Let Claude control a computer by viewing screens and performing actions", "category": "LLM Tool Libraries", "tags": ["claude", "computer-use", "automation"]},
    {"id": "claude_vision", "name": "Claude Vision (Image Analysis)", "description": "Analyze images, screenshots, and documents using multimodal vision", "category": "LLM Tool Libraries", "tags": ["claude", "vision", "image"]},
    {"id": "langchain_agents", "name": "LangChain Agent Executor", "description": "Build reasoning agents with tool access using LangChain", "category": "AI Agent Frameworks", "tags": ["langchain", "agent", "reasoning"]},
    {"id": "crewai_framework", "name": "CrewAI Multi-Agent Framework", "description": "Orchestrate role-playing AI agents working together on complex tasks", "category": "AI Agent Frameworks", "tags": ["multi-agent", "roles", "collaboration"]},
    {"id": "semantic_kernel_framework", "name": "Microsoft Semantic Kernel", "description": "Integrate AI models into apps with plugins, planners, and memory", "category": "AI Agent Frameworks", "tags": ["semantic-kernel", "plugins", "planner"]},
    {"id": "task_planner", "name": "AI Task Planner", "description": "Decompose complex goals into ordered task sequences with dependencies", "category": "AI Agent Frameworks", "tags": ["planner", "task-decomposition", "goals"]},
    {"id": "memory_system", "name": "Agent Memory System", "description": "Provide short-term and long-term memory for AI agent conversations", "category": "AI Agent Frameworks", "tags": ["memory", "context", "persistence"]},
    {"id": "multi_agent_orchestrator", "name": "Multi-Agent Orchestrator", "description": "Coordinate multiple specialized AI agents working on shared tasks", "category": "AI Agent Frameworks", "tags": ["multi-agent", "orchestration", "coordination"]},
    {"id": "code_interpreter", "name": "Code Interpreter / Sandbox Execution", "description": "Execute code in sandboxed environments for computation and analysis", "category": "Code & Development", "tags": ["code", "execution", "sandbox"]},
    {"id": "git_operations", "name": "Git Version Control Operations", "description": "Perform git operations like commit, branch, merge, and PR creation", "category": "Code & Development", "tags": ["git", "version-control", "branches"]},
    {"id": "github_actions", "name": "GitHub Actions CI/CD", "description": "Trigger and manage GitHub Actions workflows for build, test, deploy", "category": "Code & Development", "tags": ["github", "ci-cd", "actions"]},
    {"id": "test_generator", "name": "Automated Test Generator", "description": "Generate unit, integration, and end-to-end tests from source code", "category": "Code & Development", "tags": ["testing", "test-generation", "automation"]},
    {"id": "api_connector", "name": "Universal API Connector", "description": "Connect to any REST or GraphQL API with configurable authentication", "category": "Code & Development", "tags": ["api", "rest", "graphql"]},
    {"id": "n8n_http_request", "name": "n8n HTTP Request Node", "description": "Make arbitrary HTTP requests to any REST API endpoint via n8n", "category": "Automation & Integration", "tags": ["n8n", "http", "api"]},
    {"id": "n8n_webhook", "name": "n8n Webhook Trigger", "description": "Receive and process incoming webhooks to trigger n8n workflows", "category": "Automation & Integration", "tags": ["n8n", "webhook", "trigger"]},
    {"id": "zapier_email", "name": "Zapier Send Email Action", "description": "Send transactional or notification emails through Zapier automations", "category": "Automation & Integration", "tags": ["zapier", "email", "notifications"]},
    {"id": "workflow_automation", "name": "Workflow Automation Engine", "description": "Build multi-step automated workflows with conditional branching", "category": "Automation & Integration", "tags": ["workflow", "automation", "branching"]},
    {"id": "email_sender", "name": "Email Sending Service", "description": "Send transactional and notification emails via SendGrid, SES, or SMTP", "category": "Communication & Collaboration", "tags": ["email", "sendgrid", "notifications"]},
    {"id": "calendar_scheduling", "name": "Calendar & Scheduling", "description": "Create, update, and manage calendar events across Google and Outlook", "category": "Communication & Collaboration", "tags": ["calendar", "scheduling", "google"]},
    {"id": "ticket_creation", "name": "Issue/Ticket Creation", "description": "Create and manage tickets in Jira, Linear, or GitHub Issues", "category": "Communication & Collaboration", "tags": ["tickets", "jira", "project-management"]},
    {"id": "notification_hub", "name": "Multi-Channel Notification Hub", "description": "Route notifications to email, Slack, SMS, push, or webhook channels", "category": "Communication & Collaboration", "tags": ["notifications", "multi-channel", "routing"]},
    {"id": "prometheus_monitoring", "name": "Prometheus Metrics Collection", "description": "Collect, store, and query application metrics with Prometheus", "category": "Monitoring & Observability", "tags": ["prometheus", "metrics", "monitoring"]},
    {"id": "error_tracker", "name": "Error Tracking (Sentry)", "description": "Capture, track, and alert on application errors with Sentry", "category": "Monitoring & Observability", "tags": ["sentry", "errors", "tracking"]},
    {"id": "log_aggregator", "name": "Log Aggregation & Analysis", "description": "Collect and analyze application logs with ELK, Loki, or CloudWatch", "category": "Monitoring & Observability", "tags": ["logging", "elk", "analysis"]},
    {"id": "oauth_provider", "name": "OAuth 2.0 / OIDC Provider", "description": "Implement OAuth 2.0 and OpenID Connect authentication flows", "category": "Security & Auth", "tags": ["oauth", "oidc", "authentication"]},
    {"id": "secrets_manager", "name": "Secrets Manager (Vault/AWS)", "description": "Store and retrieve secrets securely via HashiCorp Vault or AWS", "category": "Security & Auth", "tags": ["secrets", "vault", "security"]},
    {"id": "rbac_engine", "name": "Role-Based Access Control Engine", "description": "Enforce fine-grained permissions based on user roles", "category": "Security & Auth", "tags": ["rbac", "permissions", "authorization"]},
    {"id": "vulnerability_scanner", "name": "Security Vulnerability Scanner", "description": "Scan code and dependencies for known security vulnerabilities", "category": "Security & Auth", "tags": ["security", "vulnerabilities", "scanning"]},
    {"id": "guardrails", "name": "AI Guardrails & Safety Filters", "description": "Add content filtering, PII detection, and safety guardrails to LLMs", "category": "AI Agent Frameworks", "tags": ["guardrails", "safety", "pii"]},
    {"id": "content_generator", "name": "Content Generation Engine", "description": "Generate blog posts, social media content, and marketing copy", "category": "Communication & Collaboration", "tags": ["content", "marketing", "copywriting"]},
    {"id": "pdf_generator", "name": "PDF Report Generator", "description": "Generate formatted PDF reports and documents from templates", "category": "Data & RAG", "tags": ["pdf", "reports", "documents"]},
    {"id": "vector_db_chromadb", "name": "ChromaDB Vector Store", "description": "Open-source embedding database for building AI apps with retrieval", "category": "Data & RAG", "tags": ["chroma", "vector-db", "embeddings"]},
    {"id": "etl_pipeline", "name": "ETL Data Pipeline", "description": "Extract, transform, and load data between systems and databases", "category": "Data & RAG", "tags": ["etl", "data-pipeline", "transformation"]},
    {"id": "payment_processing", "name": "Payment Processing Integration", "description": "Accept payments with Stripe, PayPal, or Square", "category": "Automation & Integration", "tags": ["payments", "stripe", "subscriptions"]},

    # ── NEW: MCP Servers (~15) ──────────────────────────────────────────
    {"id": "mcp_google_drive", "name": "MCP Google Drive", "description": "Manage Google Drive files, folders, and sharing via MCP", "category": "MCP Servers", "tags": ["google-drive", "mcp", "file-storage", "cloud"]},
    {"id": "mcp_notion", "name": "MCP Notion", "description": "Create and manage Notion pages, databases, and workspaces via MCP", "category": "MCP Servers", "tags": ["notion", "mcp", "wiki", "productivity"]},
    {"id": "mcp_jira", "name": "MCP Jira", "description": "Create, update, and query Jira issues and boards via MCP", "category": "MCP Servers", "tags": ["jira", "mcp", "project-management", "tickets"]},
    {"id": "mcp_confluence", "name": "MCP Confluence", "description": "Read and write Confluence wiki pages and spaces via MCP", "category": "MCP Servers", "tags": ["confluence", "mcp", "wiki", "documentation"]},
    {"id": "mcp_aws", "name": "MCP AWS", "description": "Manage AWS services like EC2, S3, and Lambda via MCP", "category": "MCP Servers", "tags": ["aws", "mcp", "cloud", "infrastructure"]},
    {"id": "mcp_docker", "name": "MCP Docker", "description": "Build, run, and manage Docker containers and images via MCP", "category": "MCP Servers", "tags": ["docker", "mcp", "containers", "devops"]},
    {"id": "mcp_kubernetes", "name": "MCP Kubernetes", "description": "Deploy and manage Kubernetes pods, services, and clusters via MCP", "category": "MCP Servers", "tags": ["kubernetes", "mcp", "orchestration", "containers"]},
    {"id": "mcp_redis", "name": "MCP Redis", "description": "Perform Redis cache read, write, and pub/sub operations via MCP", "category": "MCP Servers", "tags": ["redis", "mcp", "cache", "pub-sub"]},
    {"id": "mcp_mongodb", "name": "MCP MongoDB", "description": "Query and manage MongoDB collections and documents via MCP", "category": "MCP Servers", "tags": ["mongodb", "mcp", "nosql", "database"]},
    {"id": "mcp_elasticsearch", "name": "MCP Elasticsearch", "description": "Index, search, and analyze data in Elasticsearch via MCP", "category": "MCP Servers", "tags": ["elasticsearch", "mcp", "search", "analytics"]},
    {"id": "mcp_stripe", "name": "MCP Stripe", "description": "Process payments, subscriptions, and invoices via Stripe MCP", "category": "MCP Servers", "tags": ["stripe", "mcp", "payments", "billing"]},
    {"id": "mcp_twilio", "name": "MCP Twilio", "description": "Send SMS, voice calls, and WhatsApp messages via Twilio MCP", "category": "MCP Servers", "tags": ["twilio", "mcp", "sms", "messaging"]},
    {"id": "mcp_sqlite", "name": "MCP SQLite", "description": "Create, query, and manage SQLite databases locally via MCP", "category": "MCP Servers", "tags": ["sqlite", "mcp", "database", "local"]},
    {"id": "mcp_s3", "name": "MCP S3", "description": "Upload, download, and manage AWS S3 objects and buckets via MCP", "category": "MCP Servers", "tags": ["s3", "mcp", "storage", "aws"]},
    {"id": "mcp_cloudflare", "name": "MCP Cloudflare", "description": "Manage Cloudflare DNS, CDN, and edge workers via MCP", "category": "MCP Servers", "tags": ["cloudflare", "mcp", "cdn", "dns"]},

    # ── NEW: AI Agent Frameworks (~12) ──────────────────────────────────
    {"id": "autogen_framework", "name": "Microsoft AutoGen", "description": "Build multi-agent conversations with customizable AI personas", "category": "AI Agent Frameworks", "tags": ["autogen", "multi-agent", "microsoft", "conversations"]},
    {"id": "openai_assistants", "name": "OpenAI Assistants API", "description": "Create persistent AI assistants with tool use and file access", "category": "AI Agent Frameworks", "tags": ["openai", "assistants", "tool-use", "threads"]},
    {"id": "anthropic_tool_use", "name": "Anthropic Claude Tool Use", "description": "Integrate Claude function calling for structured tool interactions", "category": "AI Agent Frameworks", "tags": ["anthropic", "claude", "tool-use", "function-calling"]},
    {"id": "llama_index_agents", "name": "LlamaIndex Data Agents", "description": "Build data-aware agents that query and reason over documents", "category": "AI Agent Frameworks", "tags": ["llama-index", "agents", "data", "retrieval"]},
    {"id": "haystack_agents", "name": "Haystack AI Pipelines", "description": "Compose modular NLP and RAG pipelines with Haystack framework", "category": "AI Agent Frameworks", "tags": ["haystack", "pipelines", "nlp", "rag"]},
    {"id": "agency_swarm", "name": "Agency Swarm", "description": "Orchestrate collaborative agent swarms with role-based delegation", "category": "AI Agent Frameworks", "tags": ["agency-swarm", "multi-agent", "delegation", "swarm"]},
    {"id": "phidata_agents", "name": "Phidata AI Assistants", "description": "Build production-ready AI assistants with memory and tool access", "category": "AI Agent Frameworks", "tags": ["phidata", "assistants", "memory", "production"]},
    {"id": "superagi_framework", "name": "SuperAGI Framework", "description": "Deploy and manage autonomous AI agents with goal-driven execution", "category": "AI Agent Frameworks", "tags": ["superagi", "autonomous", "agents", "goals"]},
    {"id": "camel_framework", "name": "CAMEL Framework", "description": "Enable communicative AI agents for cooperative task completion", "category": "AI Agent Frameworks", "tags": ["camel", "communicative", "cooperative", "agents"]},
    {"id": "babyagi_framework", "name": "BabyAGI Task Agent", "description": "Run task-driven autonomous agents that plan and execute iteratively", "category": "AI Agent Frameworks", "tags": ["babyagi", "task-driven", "autonomous", "planning"]},
    {"id": "agent_protocol", "name": "Agent Protocol Standard", "description": "Implement the standard Agent Protocol interface for interoperability", "category": "AI Agent Frameworks", "tags": ["agent-protocol", "standard", "interop", "api"]},
    {"id": "reflexion_agent", "name": "Reflexion Agent", "description": "Build self-reflecting agents that improve through iterative feedback", "category": "AI Agent Frameworks", "tags": ["reflexion", "self-reflection", "feedback", "improvement"]},

    # ── NEW: LLM Tool Libraries (~10) ───────────────────────────────────
    {"id": "openai_function_calling", "name": "OpenAI Function Calling", "description": "Define and invoke structured functions via OpenAI chat completions", "category": "LLM Tool Libraries", "tags": ["openai", "function-calling", "structured", "chat"]},
    {"id": "gemini_tool_use", "name": "Google Gemini Tool Use", "description": "Integrate Google Gemini function calling for tool interactions", "category": "LLM Tool Libraries", "tags": ["gemini", "google", "function-calling", "tools"]},
    {"id": "cohere_tool_use", "name": "Cohere Command-R Tool Use", "description": "Use Cohere Command-R models with grounded tool-use capabilities", "category": "LLM Tool Libraries", "tags": ["cohere", "command-r", "tool-use", "grounding"]},
    {"id": "mistral_function_calling", "name": "Mistral AI Function Calling", "description": "Call structured functions through Mistral AI chat models", "category": "LLM Tool Libraries", "tags": ["mistral", "function-calling", "chat", "tools"]},
    {"id": "llm_structured_output", "name": "LLM Structured Output", "description": "Extract validated JSON and structured data from LLM responses", "category": "LLM Tool Libraries", "tags": ["structured-output", "json", "validation", "schema"]},
    {"id": "prompt_engineering_toolkit", "name": "Prompt Engineering Toolkit", "description": "Manage prompt templates, versioning, and optimization workflows", "category": "LLM Tool Libraries", "tags": ["prompts", "templates", "optimization", "versioning"]},
    {"id": "llm_router", "name": "LLM Router", "description": "Route requests across multiple LLM providers with smart fallback", "category": "LLM Tool Libraries", "tags": ["routing", "multi-model", "fallback", "load-balancing"]},
    {"id": "token_counter", "name": "Token Counter", "description": "Count tokens and manage context window limits across LLM providers", "category": "LLM Tool Libraries", "tags": ["tokens", "context-window", "counting", "limits"]},
    {"id": "llm_cache", "name": "LLM Response Cache", "description": "Cache LLM responses to reduce latency and API costs", "category": "LLM Tool Libraries", "tags": ["cache", "performance", "cost-reduction", "latency"]},
    {"id": "llm_eval_harness", "name": "LLM Evaluation Harness", "description": "Benchmark and evaluate LLM outputs with automated test suites", "category": "LLM Tool Libraries", "tags": ["evaluation", "benchmarks", "testing", "quality"]},

    # ── NEW: Cloud & Infrastructure (~15) ───────────────────────────────
    {"id": "aws_lambda", "name": "AWS Lambda", "description": "Deploy and manage serverless functions on AWS Lambda", "category": "Cloud & Infrastructure", "tags": ["aws", "lambda", "serverless", "functions"]},
    {"id": "aws_ecs", "name": "AWS ECS", "description": "Orchestrate Docker containers on AWS Elastic Container Service", "category": "Cloud & Infrastructure", "tags": ["aws", "ecs", "containers", "orchestration"]},
    {"id": "aws_sqs", "name": "AWS SQS", "description": "Send and receive messages using AWS Simple Queue Service", "category": "Cloud & Infrastructure", "tags": ["aws", "sqs", "queue", "messaging"]},
    {"id": "aws_dynamodb", "name": "AWS DynamoDB", "description": "Read and write data in AWS DynamoDB NoSQL key-value store", "category": "Cloud & Infrastructure", "tags": ["aws", "dynamodb", "nosql", "database"]},
    {"id": "gcp_cloud_run", "name": "Google Cloud Run", "description": "Deploy containerized applications on Google Cloud Run serverless", "category": "Cloud & Infrastructure", "tags": ["gcp", "cloud-run", "serverless", "containers"]},
    {"id": "gcp_bigquery", "name": "Google BigQuery", "description": "Run analytics queries on petabyte-scale data in BigQuery", "category": "Cloud & Infrastructure", "tags": ["gcp", "bigquery", "analytics", "data-warehouse"]},
    {"id": "gcp_pubsub", "name": "Google Cloud Pub/Sub", "description": "Publish and subscribe to real-time event streams on GCP", "category": "Cloud & Infrastructure", "tags": ["gcp", "pubsub", "messaging", "events"]},
    {"id": "azure_functions", "name": "Azure Functions", "description": "Deploy event-driven serverless functions on Microsoft Azure", "category": "Cloud & Infrastructure", "tags": ["azure", "functions", "serverless", "events"]},
    {"id": "azure_cosmos_db", "name": "Azure Cosmos DB", "description": "Manage globally distributed multi-model databases on Azure", "category": "Cloud & Infrastructure", "tags": ["azure", "cosmos-db", "nosql", "global"]},
    {"id": "terraform_iac", "name": "Terraform IaC", "description": "Define and provision cloud infrastructure using Terraform HCL", "category": "Cloud & Infrastructure", "tags": ["terraform", "iac", "provisioning", "hcl"]},
    {"id": "pulumi_iac", "name": "Pulumi IaC", "description": "Manage cloud infrastructure as code using familiar programming languages", "category": "Cloud & Infrastructure", "tags": ["pulumi", "iac", "infrastructure", "code"]},
    {"id": "cloudflare_workers", "name": "Cloudflare Workers", "description": "Deploy serverless functions at the edge with Cloudflare Workers", "category": "Cloud & Infrastructure", "tags": ["cloudflare", "workers", "edge", "serverless"]},
    {"id": "vercel_deployment", "name": "Vercel Deployment", "description": "Deploy and manage frontend applications on Vercel platform", "category": "Cloud & Infrastructure", "tags": ["vercel", "deployment", "frontend", "hosting"]},
    {"id": "docker_compose", "name": "Docker Compose", "description": "Define and run multi-container applications with Docker Compose", "category": "Cloud & Infrastructure", "tags": ["docker", "compose", "multi-container", "local"]},
    {"id": "nginx_reverse_proxy", "name": "Nginx Reverse Proxy", "description": "Configure Nginx for reverse proxying, load balancing, and TLS", "category": "Cloud & Infrastructure", "tags": ["nginx", "reverse-proxy", "load-balancing", "tls"]},

    # ── NEW: ML & Data Science (~15) ────────────────────────────────────
    {"id": "scikit_learn", "name": "Scikit-learn", "description": "Train and evaluate classical ML models with scikit-learn", "category": "ML & Data Science", "tags": ["scikit-learn", "ml", "classification", "regression"]},
    {"id": "pytorch_training", "name": "PyTorch Training", "description": "Build and train deep learning models using PyTorch framework", "category": "ML & Data Science", "tags": ["pytorch", "deep-learning", "training", "neural-networks"]},
    {"id": "tensorflow_serving", "name": "TensorFlow Serving", "description": "Serve trained TensorFlow models via REST and gRPC endpoints", "category": "ML & Data Science", "tags": ["tensorflow", "serving", "inference", "deployment"]},
    {"id": "huggingface_transformers", "name": "HuggingFace Transformers", "description": "Use pretrained transformer models for NLP, vision, and audio tasks", "category": "ML & Data Science", "tags": ["huggingface", "transformers", "pretrained", "nlp"]},
    {"id": "mlflow_tracking", "name": "MLflow Tracking", "description": "Track experiments, parameters, metrics, and artifacts with MLflow", "category": "ML & Data Science", "tags": ["mlflow", "experiment-tracking", "metrics", "artifacts"]},
    {"id": "wandb_tracking", "name": "Weights & Biases Tracking", "description": "Log and visualize ML experiments with Weights and Biases", "category": "ML & Data Science", "tags": ["wandb", "experiment-tracking", "visualization", "ml"]},
    {"id": "feature_store", "name": "Feature Store", "description": "Store and serve ML features for training and inference pipelines", "category": "ML & Data Science", "tags": ["feature-store", "ml-pipeline", "features", "serving"]},
    {"id": "model_registry", "name": "Model Registry", "description": "Version, stage, and manage ML models in a central registry", "category": "ML & Data Science", "tags": ["model-registry", "versioning", "staging", "ml"]},
    {"id": "data_labeling", "name": "Data Labeling", "description": "Label and annotate datasets for supervised machine learning tasks", "category": "ML & Data Science", "tags": ["labeling", "annotation", "datasets", "supervised"]},
    {"id": "anomaly_detection", "name": "Anomaly Detection", "description": "Detect statistical outliers and anomalies in time series data", "category": "ML & Data Science", "tags": ["anomaly-detection", "outliers", "time-series", "statistics"]},
    {"id": "time_series_forecasting", "name": "Time Series Forecasting", "description": "Predict future values from historical time series data patterns", "category": "ML & Data Science", "tags": ["time-series", "forecasting", "prediction", "trends"]},
    {"id": "recommendation_engine", "name": "Recommendation Engine", "description": "Generate personalized recommendations using collaborative and content filtering", "category": "ML & Data Science", "tags": ["recommendations", "collaborative-filtering", "personalization", "ml"]},
    {"id": "nlp_pipeline", "name": "NLP Pipeline", "description": "Process text with tokenization, NER, sentiment, and summarization", "category": "ML & Data Science", "tags": ["nlp", "tokenization", "ner", "sentiment"]},
    {"id": "computer_vision_pipeline", "name": "Computer Vision Pipeline", "description": "Classify images and detect objects using deep learning models", "category": "ML & Data Science", "tags": ["computer-vision", "image-classification", "object-detection", "deep-learning"]},
    {"id": "ab_testing", "name": "A/B Testing", "description": "Design and analyze A/B experiments with statistical significance testing", "category": "ML & Data Science", "tags": ["ab-testing", "experiments", "statistics", "significance"]},

    # ── NEW: Data & RAG (~10) ───────────────────────────────────────────
    {"id": "pinecone_vectordb", "name": "Pinecone Vector Database", "description": "Store and query high-dimensional vectors in Pinecone managed service", "category": "Data & RAG", "tags": ["pinecone", "vector-db", "similarity-search", "managed"]},
    {"id": "weaviate_vectordb", "name": "Weaviate Vector Search", "description": "Perform semantic vector search with Weaviate open-source engine", "category": "Data & RAG", "tags": ["weaviate", "vector-search", "semantic", "open-source"]},
    {"id": "qdrant_vectordb", "name": "Qdrant Vector Database", "description": "Run fast vector similarity search with filtering using Qdrant", "category": "Data & RAG", "tags": ["qdrant", "vector-db", "similarity", "filtering"]},
    {"id": "milvus_vectordb", "name": "Milvus Vector Database", "description": "Scale vector similarity search with Milvus open-source database", "category": "Data & RAG", "tags": ["milvus", "vector-db", "scalable", "open-source"]},
    {"id": "knowledge_graph", "name": "Knowledge Graph", "description": "Build and query knowledge graphs for structured entity relationships", "category": "Data & RAG", "tags": ["knowledge-graph", "entities", "relationships", "graph"]},
    {"id": "data_validation", "name": "Data Validation Pipeline", "description": "Validate data quality, schemas, and constraints in ETL pipelines", "category": "Data & RAG", "tags": ["validation", "data-quality", "schemas", "constraints"]},
    {"id": "stream_processing", "name": "Stream Processing", "description": "Process real-time data streams with Kafka, Flink, or Spark Streaming", "category": "Data & RAG", "tags": ["streaming", "kafka", "flink", "real-time"]},
    {"id": "data_catalog", "name": "Data Catalog", "description": "Catalog and manage metadata for datasets across the organization", "category": "Data & RAG", "tags": ["catalog", "metadata", "discovery", "governance"]},
    {"id": "graph_database", "name": "Graph Database (Neo4j)", "description": "Store and traverse graph data with Neo4j Cypher queries", "category": "Data & RAG", "tags": ["neo4j", "graph", "cypher", "relationships"]},
    {"id": "cache_layer", "name": "Cache Layer", "description": "Implement Redis or Memcached caching for low-latency data access", "category": "Data & RAG", "tags": ["cache", "redis", "memcached", "performance"]},

    # ── NEW: Code & Development (~10) ───────────────────────────────────
    {"id": "code_review_bot", "name": "Code Review Bot", "description": "Automate code review with AI-powered suggestions and linting", "category": "Code & Development", "tags": ["code-review", "automation", "linting", "suggestions"]},
    {"id": "dependency_manager", "name": "Dependency Manager", "description": "Track, update, and audit project dependencies for security risks", "category": "Code & Development", "tags": ["dependencies", "updates", "security", "audit"]},
    {"id": "api_documentation", "name": "API Documentation", "description": "Generate OpenAPI and Swagger documentation from API source code", "category": "Code & Development", "tags": ["openapi", "swagger", "documentation", "api"]},
    {"id": "database_migration", "name": "Database Migration", "description": "Manage database schema migrations with versioned changesets", "category": "Code & Development", "tags": ["migration", "schema", "database", "versioning"]},
    {"id": "code_formatter", "name": "Code Formatter", "description": "Format and lint code automatically with Prettier, Black, or ESLint", "category": "Code & Development", "tags": ["formatting", "linting", "prettier", "black"]},
    {"id": "package_publisher", "name": "Package Publisher", "description": "Build and publish packages to NPM, PyPI, or other registries", "category": "Code & Development", "tags": ["npm", "pypi", "publishing", "packages"]},
    {"id": "monorepo_tools", "name": "Monorepo Tools", "description": "Manage monorepos with Turborepo, Nx, or Lerna build systems", "category": "Code & Development", "tags": ["monorepo", "turborepo", "nx", "build"]},
    {"id": "graphql_server", "name": "GraphQL Server", "description": "Build type-safe GraphQL APIs with schema-first or code-first approach", "category": "Code & Development", "tags": ["graphql", "api", "schema", "type-safe"]},
    {"id": "websocket_server", "name": "WebSocket Server", "description": "Implement real-time bidirectional communication with WebSocket protocol", "category": "Code & Development", "tags": ["websocket", "real-time", "bidirectional", "communication"]},
    {"id": "grpc_service", "name": "gRPC Service", "description": "Define and implement high-performance gRPC services with protobuf", "category": "Code & Development", "tags": ["grpc", "protobuf", "rpc", "high-performance"]},

    # ── NEW: Frontend & UI (~12) ────────────────────────────────────────
    {"id": "react_components", "name": "React Components", "description": "Build reusable React UI components with hooks and TypeScript", "category": "Frontend & UI", "tags": ["react", "components", "hooks", "typescript"]},
    {"id": "nextjs_framework", "name": "Next.js Framework", "description": "Build full-stack React apps with SSR, routing, and API routes", "category": "Frontend & UI", "tags": ["nextjs", "react", "ssr", "full-stack"]},
    {"id": "tailwind_css", "name": "Tailwind CSS", "description": "Style interfaces rapidly with Tailwind utility-first CSS classes", "category": "Frontend & UI", "tags": ["tailwind", "css", "utility-first", "styling"]},
    {"id": "component_storybook", "name": "Storybook", "description": "Document and test UI components in isolation with Storybook", "category": "Frontend & UI", "tags": ["storybook", "components", "documentation", "testing"]},
    {"id": "state_management", "name": "State Management", "description": "Manage application state with Redux, Zustand, or Jotai libraries", "category": "Frontend & UI", "tags": ["state", "redux", "zustand", "jotai"]},
    {"id": "form_validation", "name": "Form Validation", "description": "Validate forms with Zod, Yup, or React Hook Form libraries", "category": "Frontend & UI", "tags": ["forms", "validation", "zod", "react-hook-form"]},
    {"id": "responsive_design", "name": "Responsive Design", "description": "Build mobile-first responsive layouts with CSS Grid and Flexbox", "category": "Frontend & UI", "tags": ["responsive", "mobile-first", "grid", "flexbox"]},
    {"id": "accessibility_toolkit", "name": "Accessibility Toolkit", "description": "Ensure WCAG compliance with automated accessibility auditing tools", "category": "Frontend & UI", "tags": ["accessibility", "wcag", "a11y", "audit"]},
    {"id": "animation_library", "name": "Animation Library", "description": "Create smooth UI animations with Framer Motion or GSAP", "category": "Frontend & UI", "tags": ["animation", "framer-motion", "gsap", "transitions"]},
    {"id": "chart_visualization", "name": "Chart Visualization", "description": "Render interactive charts and graphs with Chart.js or D3.js", "category": "Frontend & UI", "tags": ["charts", "d3", "visualization", "interactive"]},
    {"id": "design_system", "name": "Design System", "description": "Manage design tokens, themes, and component standards centrally", "category": "Frontend & UI", "tags": ["design-system", "tokens", "themes", "standards"]},
    {"id": "internationalization", "name": "Internationalization (i18n)", "description": "Add multi-language translation and locale support to applications", "category": "Frontend & UI", "tags": ["i18n", "translation", "locale", "multi-language"]},

    # ── NEW: Automation & Integration (~8) ──────────────────────────────
    {"id": "cron_scheduler", "name": "Cron Scheduler", "description": "Schedule recurring jobs and tasks with cron-based time expressions", "category": "Automation & Integration", "tags": ["cron", "scheduling", "jobs", "recurring"]},
    {"id": "event_bus", "name": "Event Bus", "description": "Decouple services with publish-subscribe event-driven messaging", "category": "Automation & Integration", "tags": ["events", "pub-sub", "decoupling", "messaging"]},
    {"id": "message_queue", "name": "Message Queue", "description": "Queue and process async messages with RabbitMQ or AWS SQS", "category": "Automation & Integration", "tags": ["queue", "rabbitmq", "sqs", "async"]},
    {"id": "api_gateway", "name": "API Gateway", "description": "Route, rate-limit, and authenticate API traffic through a gateway", "category": "Automation & Integration", "tags": ["api-gateway", "routing", "rate-limiting", "authentication"]},
    {"id": "oauth_integration", "name": "OAuth Integration", "description": "Connect to third-party services using OAuth authorization flows", "category": "Automation & Integration", "tags": ["oauth", "third-party", "authorization", "integration"]},
    {"id": "data_sync", "name": "Data Sync", "description": "Synchronize data bidirectionally between systems and databases", "category": "Automation & Integration", "tags": ["sync", "bidirectional", "replication", "consistency"]},
    {"id": "batch_processor", "name": "Batch Processor", "description": "Process large datasets in configurable batches with retry logic", "category": "Automation & Integration", "tags": ["batch", "processing", "bulk", "retry"]},
    {"id": "file_watcher", "name": "File Watcher", "description": "Monitor file system changes and trigger handlers automatically", "category": "Automation & Integration", "tags": ["file-watcher", "filesystem", "events", "triggers"]},

    # ── NEW: Communication & Collaboration (~8) ─────────────────────────
    {"id": "sms_gateway", "name": "SMS Gateway", "description": "Send and receive SMS messages via Twilio or Vonage APIs", "category": "Communication & Collaboration", "tags": ["sms", "twilio", "vonage", "messaging"]},
    {"id": "push_notifications", "name": "Push Notifications", "description": "Deliver mobile and web push notifications to user devices", "category": "Communication & Collaboration", "tags": ["push", "notifications", "mobile", "web"]},
    {"id": "video_conferencing", "name": "Video Conferencing API", "description": "Integrate video calls via Zoom or Microsoft Teams REST APIs", "category": "Communication & Collaboration", "tags": ["video", "zoom", "teams", "conferencing"]},
    {"id": "chatbot_framework", "name": "Chatbot Framework", "description": "Build customer support chatbots with intent routing and NLU", "category": "Communication & Collaboration", "tags": ["chatbot", "support", "nlu", "intent"]},
    {"id": "helpdesk_integration", "name": "Helpdesk Integration", "description": "Create and manage support tickets in Zendesk or Freshdesk", "category": "Communication & Collaboration", "tags": ["helpdesk", "zendesk", "freshdesk", "tickets"]},
    {"id": "team_wiki", "name": "Team Wiki", "description": "Build and maintain an internal team knowledge base and wiki", "category": "Communication & Collaboration", "tags": ["wiki", "knowledge-base", "documentation", "internal"]},
    {"id": "real_time_chat", "name": "Real-Time Chat", "description": "Implement live chat with Socket.io or WebSocket connections", "category": "Communication & Collaboration", "tags": ["chat", "socket-io", "websocket", "real-time"]},
    {"id": "survey_builder", "name": "Survey Builder", "description": "Create surveys and collect structured user feedback and responses", "category": "Communication & Collaboration", "tags": ["survey", "feedback", "forms", "responses"]},

    # ── NEW: DevOps & Deployment (~10) ──────────────────────────────────
    {"id": "github_actions_advanced", "name": "Advanced GitHub Actions", "description": "Build complex CI/CD pipelines with matrix builds and reusable workflows", "category": "DevOps & Deployment", "tags": ["github-actions", "ci-cd", "matrix", "reusable"]},
    {"id": "gitlab_ci", "name": "GitLab CI/CD", "description": "Configure multi-stage CI/CD pipelines in GitLab YAML configuration", "category": "DevOps & Deployment", "tags": ["gitlab", "ci-cd", "pipelines", "yaml"]},
    {"id": "jenkins_pipeline", "name": "Jenkins Pipeline", "description": "Define and manage Jenkins build pipelines with Groovy DSL", "category": "DevOps & Deployment", "tags": ["jenkins", "pipeline", "groovy", "builds"]},
    {"id": "argocd", "name": "ArgoCD GitOps", "description": "Deploy Kubernetes applications using GitOps with ArgoCD sync", "category": "DevOps & Deployment", "tags": ["argocd", "gitops", "kubernetes", "sync"]},
    {"id": "helm_charts", "name": "Helm Charts", "description": "Package and deploy Kubernetes applications using Helm charts", "category": "DevOps & Deployment", "tags": ["helm", "kubernetes", "charts", "packaging"]},
    {"id": "ansible_playbooks", "name": "Ansible Playbooks", "description": "Automate server configuration and deployment with Ansible YAML", "category": "DevOps & Deployment", "tags": ["ansible", "automation", "configuration", "yaml"]},
    {"id": "container_registry", "name": "Container Registry", "description": "Store and distribute container images via Docker Hub or ECR", "category": "DevOps & Deployment", "tags": ["registry", "docker", "ecr", "images"]},
    {"id": "blue_green_deploy", "name": "Blue-Green Deployment", "description": "Achieve zero-downtime releases with blue-green deployment strategy", "category": "DevOps & Deployment", "tags": ["blue-green", "zero-downtime", "deployment", "release"]},
    {"id": "feature_flags", "name": "Feature Flags", "description": "Toggle features at runtime with LaunchDarkly or Unleash flags", "category": "DevOps & Deployment", "tags": ["feature-flags", "launchdarkly", "toggles", "runtime"]},
    {"id": "rollback_automation", "name": "Rollback Automation", "description": "Automatically roll back failed deployments to last stable version", "category": "DevOps & Deployment", "tags": ["rollback", "automation", "recovery", "stable"]},

    # ── NEW: Testing & QA (~10) ─────────────────────────────────────────
    {"id": "playwright_testing", "name": "Playwright Testing", "description": "Automate cross-browser end-to-end tests with Playwright framework", "category": "Testing & QA", "tags": ["playwright", "e2e", "browser", "cross-browser"]},
    {"id": "cypress_testing", "name": "Cypress Testing", "description": "Write fast and reliable end-to-end tests with Cypress runner", "category": "Testing & QA", "tags": ["cypress", "e2e", "testing", "frontend"]},
    {"id": "jest_unit_testing", "name": "Jest Unit Testing", "description": "Run JavaScript and TypeScript unit tests with Jest framework", "category": "Testing & QA", "tags": ["jest", "unit-testing", "javascript", "typescript"]},
    {"id": "pytest_framework", "name": "Pytest Framework", "description": "Write and run Python tests with fixtures and parameterization", "category": "Testing & QA", "tags": ["pytest", "python", "fixtures", "testing"]},
    {"id": "load_testing", "name": "Load Testing", "description": "Simulate high traffic with k6, Locust, or Artillery load tests", "category": "Testing & QA", "tags": ["load-testing", "k6", "locust", "performance"]},
    {"id": "api_contract_testing", "name": "API Contract Testing", "description": "Verify API contracts between services using Pact or Dredd", "category": "Testing & QA", "tags": ["contract-testing", "pact", "api", "consumer-driven"]},
    {"id": "visual_regression", "name": "Visual Regression Testing", "description": "Detect unintended UI changes with screenshot comparison testing", "category": "Testing & QA", "tags": ["visual-regression", "screenshots", "ui", "comparison"]},
    {"id": "chaos_engineering", "name": "Chaos Engineering", "description": "Inject faults to test system resilience with chaos experiments", "category": "Testing & QA", "tags": ["chaos", "resilience", "fault-injection", "reliability"]},
    {"id": "test_data_generator", "name": "Test Data Generator", "description": "Generate realistic fake data for testing with Faker or factories", "category": "Testing & QA", "tags": ["test-data", "faker", "factories", "seeding"]},
    {"id": "coverage_reporter", "name": "Coverage Reporter", "description": "Measure and report code coverage across test suites and CI runs", "category": "Testing & QA", "tags": ["coverage", "reporting", "ci", "metrics"]},

    # ── NEW: Monitoring & Observability (~8) ────────────────────────────
    {"id": "grafana_dashboards", "name": "Grafana Dashboards", "description": "Build real-time monitoring dashboards with Grafana visualizations", "category": "Monitoring & Observability", "tags": ["grafana", "dashboards", "visualization", "monitoring"]},
    {"id": "datadog_apm", "name": "Datadog APM", "description": "Monitor application performance and traces with Datadog APM", "category": "Monitoring & Observability", "tags": ["datadog", "apm", "traces", "performance"]},
    {"id": "newrelic_monitoring", "name": "New Relic Monitoring", "description": "Track application health and performance with New Relic agents", "category": "Monitoring & Observability", "tags": ["newrelic", "monitoring", "health", "agents"]},
    {"id": "opentelemetry", "name": "OpenTelemetry", "description": "Instrument distributed tracing and metrics with OpenTelemetry SDK", "category": "Monitoring & Observability", "tags": ["opentelemetry", "tracing", "metrics", "distributed"]},
    {"id": "alertmanager", "name": "Prometheus Alertmanager", "description": "Route and manage alerts from Prometheus monitoring rules", "category": "Monitoring & Observability", "tags": ["alertmanager", "prometheus", "alerts", "routing"]},
    {"id": "uptime_monitoring", "name": "Uptime Monitoring", "description": "Monitor endpoint availability and response times continuously", "category": "Monitoring & Observability", "tags": ["uptime", "availability", "health-checks", "endpoints"]},
    {"id": "cost_monitoring", "name": "Cloud Cost Monitoring", "description": "Track and optimize cloud spending across AWS, GCP, and Azure", "category": "Monitoring & Observability", "tags": ["cost", "cloud", "optimization", "spending"]},
    {"id": "performance_profiler", "name": "Performance Profiler", "description": "Profile application CPU, memory, and I/O bottlenecks in production", "category": "Monitoring & Observability", "tags": ["profiling", "cpu", "memory", "bottlenecks"]},

    # ── NEW: Security & Auth (~8) ───────────────────────────────────────
    {"id": "jwt_auth", "name": "JWT Authentication", "description": "Issue and validate JSON Web Tokens for stateless authentication", "category": "Security & Auth", "tags": ["jwt", "tokens", "authentication", "stateless"]},
    {"id": "api_key_management", "name": "API Key Management", "description": "Generate, rotate, and revoke API keys for service authentication", "category": "Security & Auth", "tags": ["api-keys", "rotation", "management", "authentication"]},
    {"id": "rate_limiter", "name": "Rate Limiter", "description": "Throttle API requests with configurable rate limiting policies", "category": "Security & Auth", "tags": ["rate-limiting", "throttling", "api", "policies"]},
    {"id": "encryption_service", "name": "Encryption Service", "description": "Encrypt and decrypt data at rest and in transit with AES/RSA", "category": "Security & Auth", "tags": ["encryption", "aes", "rsa", "data-protection"]},
    {"id": "audit_logging", "name": "Audit Logging", "description": "Record security-relevant events in tamper-proof audit trail logs", "category": "Security & Auth", "tags": ["audit", "logging", "compliance", "trail"]},
    {"id": "cors_management", "name": "CORS Management", "description": "Configure and enforce cross-origin resource sharing policies", "category": "Security & Auth", "tags": ["cors", "cross-origin", "policies", "security"]},
    {"id": "ddos_protection", "name": "DDoS Protection", "description": "Mitigate distributed denial-of-service attacks with traffic filtering", "category": "Security & Auth", "tags": ["ddos", "protection", "mitigation", "traffic"]},
    {"id": "compliance_checker", "name": "Compliance Checker", "description": "Verify SOC2, GDPR, and HIPAA regulatory compliance requirements", "category": "Security & Auth", "tags": ["compliance", "soc2", "gdpr", "hipaa"]},

    # ── NEW: Media & Content (~8) ───────────────────────────────────────
    {"id": "image_processing", "name": "Image Processing", "description": "Resize, crop, compress, and transform images for web delivery", "category": "Media & Content", "tags": ["image", "resize", "compression", "optimization"]},
    {"id": "video_processing", "name": "Video Processing", "description": "Transcode, trim, and compress video files for streaming delivery", "category": "Media & Content", "tags": ["video", "transcoding", "compression", "streaming"]},
    {"id": "speech_to_text", "name": "Speech to Text", "description": "Transcribe audio recordings to text with speech recognition models", "category": "Media & Content", "tags": ["speech", "transcription", "audio", "recognition"]},
    {"id": "text_to_speech", "name": "Text to Speech", "description": "Convert text content into natural-sounding speech audio output", "category": "Media & Content", "tags": ["tts", "speech", "audio", "synthesis"]},
    {"id": "document_ocr", "name": "Document OCR", "description": "Extract text from scanned documents and images using OCR engines", "category": "Media & Content", "tags": ["ocr", "document", "text-extraction", "scanning"]},
    {"id": "media_storage", "name": "Media Storage", "description": "Store and serve media files via S3, Cloudinary, or CDN providers", "category": "Media & Content", "tags": ["storage", "s3", "cloudinary", "cdn"]},
    {"id": "content_moderation", "name": "Content Moderation", "description": "Filter and moderate user-generated content with AI safety models", "category": "Media & Content", "tags": ["moderation", "safety", "filtering", "ugc"]},
    {"id": "markdown_renderer", "name": "Markdown Renderer", "description": "Convert Markdown documents to styled HTML or PDF output", "category": "Media & Content", "tags": ["markdown", "html", "pdf", "rendering"]},
]

# ---------- LLM prompts for skill suggestion ----------

SKILL_SUGGEST_SYSTEM_PROMPT = (
    "You are an expert in AI development tools and Claude-compatible skills. "
    "Recommend the most relevant skills for a software project."
)

SKILL_SUGGEST_USER_PROMPT = """Given this project profile:
Problem: {problem_definition}
Target User: {target_user}
Value Proposition: {value_proposition}
Deployment: {deployment_type}
AI Depth: {ai_depth}

Selected features:
{feature_list}

Available skills:
{skill_list}

Select the {max_suggestions} most relevant skills for this project.
Return the top {default_selected} as "suggested" (pre-checked) and the rest as "available".

Return ONLY valid JSON:
{{"suggested": ["skill_id_1", "skill_id_2", ...], "available": ["skill_id_3", ...]}}

Rules:
- Select exactly {max_suggestions} skills total
- Top {default_selected} are "suggested" (highest priority)
- Remaining are "available" (shown but unchecked)
- Prioritize skills that align with the project's AI depth and deployment type
- Return ONLY the JSON object, no markdown"""


def load_registry() -> list[dict]:
    """Load the global skill registry from config/skill_registry.json.

    Falls back to FALLBACK_SKILLS if the file is missing or corrupt.

    Returns:
        List of skill dicts.
    """
    if not REGISTRY_PATH.exists():
        logger.info("Skill registry not found at %s, using fallback", REGISTRY_PATH)
        return [dict(s) for s in FALLBACK_SKILLS]

    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        skills = data.get("skills", [])
        if not isinstance(skills, list) or len(skills) < 10:
            logger.warning("Skill registry has too few skills (%d), using fallback", len(skills))
            return [dict(s) for s in FALLBACK_SKILLS]
        return skills
    except (json.JSONDecodeError, TypeError, OSError) as e:
        logger.warning("Failed to load skill registry: %s. Using fallback.", e)
        return [dict(s) for s in FALLBACK_SKILLS]


def get_skills_by_category(skills: list[dict]) -> list[dict]:
    """Group a flat skill list into category sections.

    Returns:
        List of dicts: [{"name": "Category", "skills": [...]}, ...]
    """
    categories: dict[str, list[dict]] = {}
    order: list[str] = []
    for skill in skills:
        cat = skill.get("category", "Custom Skills")
        if cat not in categories:
            categories[cat] = []
            order.append(cat)
        categories[cat].append(skill)
    return [{"name": cat, "skills": categories[cat]} for cat in order]


def get_skills_by_ids(skills: list[dict], skill_ids: list[str]) -> list[dict]:
    """Filter skills to those matching the given IDs.

    Args:
        skills: Full skill list.
        skill_ids: List of skill ID strings to select.

    Returns:
        List of matching skill dicts, in original order.
    """
    id_set = set(skill_ids)
    return [s for s in skills if s["id"] in id_set]


def suggest_skills(
    profile: dict,
    features: list[dict],
    registry: list[dict],
    max_suggestions: int = 50,
    default_selected: int = 15,
) -> dict:
    """Suggest skills for a project using LLM with tag-based fallback.

    Args:
        profile: The project_profile dictionary.
        features: List of selected feature dicts.
        registry: Full skill registry list.
        max_suggestions: Max total skills to suggest.
        default_selected: How many to pre-check as "suggested".

    Returns:
        Dict with "suggested" (list of skill IDs) and "available" (list of skill IDs).
    """
    if not is_available():
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)

    # Extract profile fields
    fields = {}
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth"]:
        field_data = profile.get(field_name, {})
        fields[field_name] = field_data.get("selected", "") or ""

    if not any(fields.values()):
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)

    feature_list = "\n".join(
        f"- {f['name']}: {f.get('description', '')}" for f in features[:30]
    ) or "- No features selected"

    skill_list = "\n".join(
        f"- {s['id']}: {s['name']} — {s['description']}" for s in registry
    )

    try:
        prompt = SKILL_SUGGEST_USER_PROMPT.format(
            **fields,
            feature_list=feature_list,
            skill_list=skill_list,
            max_suggestions=max_suggestions,
            default_selected=default_selected,
        )
        response = chat(
            system_prompt=SKILL_SUGGEST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            response_format={"type": "json_object"},
        )
        return _parse_suggestion_response(
            response.content, registry, max_suggestions, default_selected,
        )
    except (LLMUnavailableError, LLMClientError) as e:
        logger.warning("LLM skill suggestion failed: %s. Using tag matching.", e)
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)
    except Exception as e:
        logger.warning("Unexpected error suggesting skills: %s. Using tag matching.", e)
        return _match_skills_by_tags(profile, features, registry, max_suggestions, default_selected)


def _parse_suggestion_response(
    raw_json: str,
    registry: list[dict],
    max_suggestions: int,
    default_selected: int,
) -> dict:
    """Parse LLM skill suggestion response."""
    valid_ids = {s["id"] for s in registry}
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return _fallback_suggestion(registry, max_suggestions, default_selected)

    suggested = [sid for sid in data.get("suggested", []) if sid in valid_ids]
    available = [sid for sid in data.get("available", []) if sid in valid_ids]

    # Ensure we have enough suggestions
    if len(suggested) < 5:
        return _fallback_suggestion(registry, max_suggestions, default_selected)

    return {"suggested": suggested[:default_selected], "available": available}


def _fallback_suggestion(
    registry: list[dict],
    max_suggestions: int,
    default_selected: int,
) -> dict:
    """Simple fallback: take first N skills from registry."""
    all_ids = [s["id"] for s in registry[:max_suggestions]]
    return {
        "suggested": all_ids[:default_selected],
        "available": all_ids[default_selected:],
    }


def _match_skills_by_tags(
    profile: dict,
    features: list[dict],
    registry: list[dict],
    max_results: int = 50,
    default_selected: int = 15,
) -> dict:
    """Deterministic fallback: keyword-match project context against skill tags.

    Scores each skill by how many of its tags match words found in the
    project profile and feature descriptions, then returns the top matches.
    """
    # Build keyword set from profile and features
    keywords: set[str] = set()
    for field_name in ["problem_definition", "target_user", "value_proposition",
                       "deployment_type", "ai_depth", "monetization_model"]:
        field_data = profile.get(field_name, {})
        selected = field_data.get("selected", "") or ""
        keywords.update(w.lower() for w in selected.split() if len(w) > 2)

    for feat in features:
        keywords.update(w.lower() for w in feat.get("name", "").split() if len(w) > 2)
        keywords.update(w.lower() for w in feat.get("description", "").split() if len(w) > 2)

    # Score each skill
    scored = []
    for skill in registry:
        tags = skill.get("tags", [])
        name_words = [w.lower() for w in skill.get("name", "").split()]
        desc_words = [w.lower() for w in skill.get("description", "").split()]
        all_terms = tags + name_words + desc_words

        score = sum(1 for term in all_terms if term in keywords)
        scored.append((score, skill["id"]))

    # Sort by score descending, then by original order for ties
    scored.sort(key=lambda x: -x[0])

    top_ids = [sid for _, sid in scored[:max_results]]
    return {
        "suggested": top_ids[:default_selected],
        "available": top_ids[default_selected:],
    }


def build_skill_chapter_context(selected_skills: list[dict]) -> str:
    """Build prompt content describing selected skills for chapter generation.

    Args:
        selected_skills: List of selected skill dicts.

    Returns:
        Formatted string for injection into chapter prompts.
    """
    if not selected_skills:
        return ""

    by_category = get_skills_by_category(selected_skills)
    lines = []
    for cat_group in by_category:
        lines.append(f"\n### {cat_group['name']}")
        for skill in cat_group["skills"]:
            lines.append(f"- **{skill['name']}**: {skill['description']}")

    return "\n".join(lines)
