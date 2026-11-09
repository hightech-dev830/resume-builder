// Master profile used as the source of truth for every tailored resume.
// Editable in-app (Base Profile tab); the copy saved in the DB wins over this seed.
module.exports = {
  basics: {
    name: 'Kevin Burgess',
    label: 'Senior Software Engineer',
    email: 'kcburgess11@gmail.com',
    phone: '310-933-9572',
    location: { city: 'Rolling Hills', region: 'CA', countryCode: 'US' },
    profiles: [
      { network: 'LinkedIn', username: 'kevin-burgess-357505446', url: 'https://www.linkedin.com/in/kevin-burgess-357505446/' }
    ],
    summary:
      'Senior Software Engineer who builds backend services, full-stack web applications and data platforms for legal, government and financial businesses. Works mainly in Python, Go, TypeScript and SQL, with Django, FastAPI, Node.js and React on PostgreSQL and SQL Server, deployed with Docker and Kubernetes on AWS and Azure. Recent work includes in-app AI assistants, permission-aware search and document and OCR pipelines.\n\nFocused on software that people in regulated industries can rely on: clear access control, careful handling of personal data, and tested changes that are reviewed before they reach production. Works closely with product owners, clients and engineers to turn requirements into a practical design, then builds it hands-on and supports it in production.',
    highlights: [
      { name: 'Backend and APIs', value: 'Python (Django, FastAPI, Flask), Go, C# / .NET, Node.js and Rust services, REST APIs, background jobs' },
      { name: 'Frontend', value: 'React, TypeScript, Redux and Angular, responsive UIs, authentication and admin tools' },
      { name: 'Data', value: 'PostgreSQL, SQL Server, MySQL, MongoDB and Redis, schema design, query tuning and ETL pipelines' },
      { name: 'Cloud and AI', value: 'Docker, Kubernetes, AWS, Azure, CI/CD, LLM agents, RAG and OCR' }
    ]
  },
  work: [
    {
      name: 'Jaburg & Wilk, P.C.',
      context: 'Law Firm – Case Management System',
      position: 'Senior Software Engineer',
      location: 'Phoenix, AZ, US',
      startDate: '2024-04',
      endDate: '2026-08',
      description:
        "Backend, data and AI engineering for the firm's practice-management platform (Python, Go, PostgreSQL, Kubernetes), which holds matters, clients, deadlines, email and iManage documents across many practice groups.",
      highlights: [
        "Built the platform's MCP server in Go: 20 tools with per-user scoped API keys and tenant isolation built in, so no tool can act outside the caller's firm or permissions.",
        'Built "Ask the Agent", an in-app assistant in Go and Python that works with Claude and self-hosted open models (Qwen via vLLM/Ollama), with streaming responses (SSE), step and token budgets, and read-only access by default.',
        'Built Python services and Airflow pipelines that sync matters, clients, deadlines, email and iManage documents into PostgreSQL, with incremental loads, retries and alerts on failed runs.',
        'Built the OCR and intake pipeline (Mistral OCR, self-hosted GLM-OCR) with provider fallback and accuracy tracking, extracting about 60 fields with Pydantic validation for attorney review.',
        'Secured search over documents, email and case data (Pinecone) with matter-level permissions, and added guardrails: strict JSON validation, a court-deadline engine that overrides model dates, and PII scrubbing.',
        'Rebuilt the CD pipeline with secret scanning, SAST (Semgrep, CodeQL), Trivy image scans, signed images and smoke-tested deploys to Kubernetes.'
      ]
    },
    {
      name: 'rTurner Consulting, LLC',
      context: 'Government data management',
      position: 'Senior Software Engineer',
      location: 'Taylorsville, KY, US',
      startDate: '2022-04',
      endDate: '2024-03',
      description:
        'GovCon+ is an AI platform for U.S. federal contractors built around the RFQPlus procurement product: solicitation ingestion, RAG chat with sources, bid-fit scoring, automated RFQ section drafting, compliance matrices and capture reports.',
      highlights: [
        'Built the backend in Python (Celery, Redis) and Rust (Axum, Tokio) that ingests SAM.gov and HigherGov solicitations daily and powers chat, bid scoring, section drafting and capture reports.',
        'Built production RAG with page-level citations, using file-type-aware PDF parsing, Tesseract OCR, and Pinecone plus SQL Server vector search behind organisation-scoped, audited stored procedures.',
        'Designed multi-step drafting pipelines (planner, writer, validator) that fill about 8 RFQ sections in parallel, with contract numbers, values and dates checked against source documents.',
        'Hardened services against prompt injection and SSRF, and screened documents for controlled unclassified information before indexing.',
        'Moved generation and embeddings from hosted OpenAI models to self-hosted Qwen on Ollama GPU servers, cutting per-call cost to near zero, with an operations console for latency and failures.',
        'Earlier, as Backend Engineer: built the RFQPlus procurement platform in C#, ASP.NET Core, SQL Server and Entity Framework, covering RFQ, supplier, quotation, pricing and approval workflows.'
      ]
    },
    {
      name: 'Internet Financial Pov. LLC',
      position: 'Software Engineer',
      location: 'Boston, MA, US',
      startDate: '2020-08',
      endDate: '2022-07',
      description:
        'Full-stack web development, third-party integrations and database work for customer-facing financial products and internal tools.',
      highlights: [
        'Built customer-facing web apps in React, TypeScript and Redux on REST APIs in Python (Django REST Framework) and Node.js (Express).',
        'Delivered onboarding and account-management features end to end, from PostgreSQL schema to API, forms and admin screens.',
        'Integrated Stripe payments, Twilio SMS and SendGrid email, with webhook handling, retries and daily reconciliation reports.',
        'Implemented JWT / OAuth 2.0 authentication, role-based access control and audit logging for sensitive account changes.',
        'Sped up pages and APIs by tuning slow SQL queries, adding Redis caching and moving long-running work to Celery jobs; built Python and Airflow ETL for finance reporting.',
        'Containerised services with Docker and set up Nginx and GitHub Actions CI/CD with automated tests, working in two-week sprints.'
      ]
    },
    {
      name: 'Asteria Learning, Inc.',
      position: 'Full-Stack Software Engineer',
      location: 'Tampa, FL, US',
      startDate: '2015-04',
      endDate: '2020-08',
      description:
        'Web applications and backend services for SaaS clients in healthcare, finance and operations, built by a small full-stack team.',
      highlights: [
        'Built full-stack features with Python (Django, Flask) and JavaScript (AngularJS, later React), from requirements to production.',
        'Developed REST APIs and Node.js (Express) services for scheduling, billing and notifications, with payment, SMS and maps integrations.',
        'Created reusable React components and responsive Bootstrap / Sass pages shared across client projects.',
        'Built reporting dashboards (Chart.js, D3.js) over PostgreSQL, MySQL and MongoDB, and migrated legacy MySQL and PHP site data into new PostgreSQL schemas.',
        'Wrote pytest and Jest tests, took part in code reviews, and fixed bugs and performance issues in live systems.',
        'Deployed and maintained applications on AWS (EC2, S3, RDS) and Heroku, including backups and routine database maintenance.'
      ]
    }
  ],
  skills: [
    { name: 'Languages', keywords: ['Python', 'JavaScript / TypeScript', 'Go', 'C#', 'SQL (T-SQL, PL/pgSQL)', 'Rust', 'Bash'] },
    { name: 'Backend', keywords: ['Python (Django, Flask, FastAPI, Celery)', 'Go', 'Rust (Axum, Tokio)', 'C# / .NET (ASP.NET Core, Entity Framework)', 'Node.js (Express)', 'REST APIs', 'SSE'] },
    { name: 'Frontend', keywords: ['React', 'Redux', 'TypeScript', 'Angular', 'Bootstrap', 'Sass'] },
    { name: 'Databases', keywords: ['PostgreSQL', 'SQL Server', 'MySQL', 'MongoDB', 'Redis', 'Elasticsearch', 'Pinecone', 'Snowflake'] },
    { name: 'Data and pipelines', keywords: ['Apache Airflow', 'dbt', 'PySpark', 'Pandas', 'ETL', 'data modeling'] },
    { name: 'Cloud and DevOps', keywords: ['AWS (EC2, S3, RDS, EKS)', 'Azure (Azure SQL, Blob Storage, Entra ID)', 'Docker', 'Kubernetes', 'GitHub Actions', 'CI/CD', 'Sentry'] },
    { name: 'AI', keywords: ['Tool-calling agents', 'MCP', 'RAG', 'embeddings', 'vector search', 'OCR', 'guardrails', 'Claude', 'GPT-4o', 'Qwen', 'Ollama', 'vLLM'] }
  ],
  education: [
    {
      institution: 'California State University Dominguez Hills',
      studyType: "Bachelor's Degree",
      area: 'Computer Science',
      startDate: '2000-05',
      endDate: '2004-05'
    }
  ],
  languages: [{ language: 'English', fluency: 'Native' }]
};
