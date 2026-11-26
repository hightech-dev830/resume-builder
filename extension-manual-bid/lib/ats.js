// ATS keyword matching. A technology dictionary (name | aliases | family | first usable year | flags)
// drives three things:
//  - extracting what a job description asks for, weighted by where and how often it appears
//  - scoring a resume against it the way ATS parsers do (exact terms, in skills and in context)
//  - realism checks (lib/validate.js): no technology before it existed, adjacent-technology families
// Flags: c = case-sensitive match (ambiguous words such as Go, Spring, Express), g = generic (practice, not a product).

const DICT = `
Python|python|lang|1991
Java|Java|jvm|1996|c
JavaScript|javascript;js;es6;ecmascript|js|1996
TypeScript|typescript|js|2012
Go|Go;Golang;golang|lang|2012|c
Rust|Rust|lang|2015|c
C++|c\\+\\+;cpp|lang|1985
C#|c#;csharp|dotnet|2002
C|C programming;ANSI C;embedded C|lang|1972|c
Ruby|Ruby|lang|1996|c
PHP|php|lang|1995
Kotlin|kotlin|jvm|2016
Swift|Swift|mobile|2014|c
Objective-C|objective-c;objc|mobile|1988
Scala|scala|jvm|2004
Elixir|elixir|lang|2012
Erlang|erlang|lang|1990
Haskell|haskell|lang|1990
Clojure|clojure|jvm|2007
Dart|Dart|lang|2013|c
Perl|perl|lang|1988
Julia|Julia|lang|2018|c
Bash|bash;shell scripting;shell|lang|1990
PowerShell|powershell|lang|2006
SQL|sql|sql|1986
T-SQL|t-sql;tsql|sql|1995
PL/SQL|pl/sql|sql|1992
Solidity|solidity|lang|2015
Groovy|groovy|jvm|2007
F#|f#|dotnet|2005
VB.NET|vb\\.net|dotnet|2002
COBOL|cobol|lang|1960
React|react;react\\.js;reactjs|fe-fw|2013
Angular|angular|fe-fw|2016
AngularJS|angularjs;angular\\.js|fe-fw|2010
Vue.js|vue;vue\\.js;vuejs|fe-fw|2014
Svelte|svelte|fe-fw|2016
Next.js|next\\.js;nextjs|fe-meta|2016
Nuxt|nuxt;nuxt\\.js|fe-meta|2016
Remix|Remix|fe-meta|2021|c
Gatsby|gatsby|fe-meta|2015
SvelteKit|sveltekit|fe-meta|2022
Astro|Astro|fe-meta|2022|c
Redux|redux|state|2015
MobX|mobx|state|2015
Zustand|zustand|state|2019
RxJS|rxjs|state|2015
jQuery|jquery|fe-lib|2006
HTML|html;html5|fe-base|1993
CSS|css;css3|fe-base|1996
Sass|sass;scss|css|2006
Tailwind CSS|tailwind;tailwind css;tailwindcss|css|2017
Bootstrap|bootstrap|css|2011
Material UI|material ui;material-ui;mui|css|2014
Styled Components|styled-components;styled components|css|2016
Webpack|webpack|build|2012
Vite|vite|build|2020
Babel|babel|build|2014
Storybook|storybook|fe-tool|2016
D3.js|d3;d3\\.js|viz|2011
Three.js|three\\.js;threejs|viz|2010
WebSockets|websockets?;socket\\.io|api|2011
WebAssembly|webassembly;wasm|lang|2017
Electron|electron|desktop|2013
Ember.js|ember;ember\\.js|fe-fw|2011
Backbone.js|backbone\\.js;backbone|fe-fw|2010
htmx|htmx|fe-lib|2020
Node.js|node;node\\.js;nodejs|node|2009
Express|Express;Express\\.js;ExpressJS|node-web|2010|c
NestJS|nestjs;nest\\.js|node-web|2017
Fastify|fastify|node-web|2016
Deno|deno|node|2020
Bun|Bun|node|2022|c
Django|django|py-web|2005
Flask|flask|py-web|2010
FastAPI|fastapi|py-web|2018
Celery|celery|queue|2009
Spring Boot|spring boot;springboot|jvm-web|2014
Spring|Spring;Spring Framework|jvm-web|2004|c
Hibernate|hibernate|orm|2001
.NET|\\.net;dotnet|dotnet|2002
.NET Core|\\.net core;\\.net [5-9]|dotnet|2016
ASP.NET|asp\\.net|dotnet|2002
ASP.NET Core|asp\\.net core|dotnet|2016
Entity Framework|entity framework;ef core|orm|2008
Ruby on Rails|rails;ruby on rails|ruby-web|2004
Laravel|laravel|php-web|2011
Symfony|symfony|php-web|2005
Gin|Gin|go-web|2014|c
Fiber|Fiber|go-web|2020|c
Actix|actix|rust-web|2017
Axum|axum|rust-web|2021
Tokio|tokio|rust-web|2016
gRPC|grpc|api|2016
GraphQL|graphql|api|2015
REST APIs|REST;RESTful;REST API;REST APIs;RESTful APIs|api|2000|cg
Apollo|apollo|api|2016
tRPC|trpc|api|2020
OpenAPI|openapi;swagger|api|2011
SOAP|soap|api|1999
Protocol Buffers|protobuf;protocol buffers|api|2008
OAuth 2.0|oauth;oauth2;oauth 2\\.0|auth|2012
OpenID Connect|oidc;openid connect|auth|2014
JWT|jwt|auth|2015
SAML|saml|auth|2005
Keycloak|keycloak|auth|2014
Auth0|auth0|auth|2013
Okta|okta|auth|2009
PostgreSQL|postgres;postgresql|db-sql|1996
MySQL|mysql|db-sql|1995
MariaDB|mariadb|db-sql|2009
SQL Server|sql server;mssql;ms sql|db-sql|1989
Oracle|Oracle;Oracle DB;Oracle Database|db-sql|1979|c
SQLite|sqlite|db-sql|2000
Aurora|aurora|db-sql|2014
CockroachDB|cockroachdb|db-sql|2015
MongoDB|mongo;mongodb|db-nosql|2009
DynamoDB|dynamodb|db-nosql|2012
Cassandra|cassandra|db-nosql|2008
Cosmos DB|cosmos ?db|db-nosql|2017
Couchbase|couchbase|db-nosql|2011
Firestore|firestore|db-nosql|2017
Firebase|firebase|baas|2012
Supabase|supabase|baas|2020
Redis|redis|cache|2009
Memcached|memcached|cache|2003
Neo4j|neo4j|db-graph|2007
ClickHouse|clickhouse|olap|2016
TimescaleDB|timescale;timescaledb|tsdb|2017
InfluxDB|influxdb|tsdb|2013
Elasticsearch|elasticsearch;elastic search|search|2010
OpenSearch|opensearch|search|2021
Solr|solr|search|2006
Lucene|lucene|search|1999
Vespa|vespa|search|2017
Algolia|algolia|search|2012
Snowflake|snowflake|dwh|2014
BigQuery|bigquery|dwh|2011
Redshift|redshift|dwh|2013
Databricks|databricks|dwh|2013
Delta Lake|delta lake|dwh|2019
Pinecone|pinecone|vector|2021
Weaviate|weaviate|vector|2019
Milvus|milvus|vector|2019
pgvector|pgvector|vector|2021
Qdrant|qdrant|vector|2021
Chroma|chromadb;chroma|vector|2022
FAISS|faiss|vector|2017
Prisma|prisma|orm|2019
Sequelize|sequelize|orm|2011
TypeORM|typeorm|orm|2016
SQLAlchemy|sqlalchemy|orm|2006
Drizzle|drizzle|orm|2022
AWS|aws;amazon web services|cloud|2006
Azure|azure;microsoft azure|cloud|2010
GCP|gcp;google cloud;google cloud platform|cloud|2008
AWS Lambda|lambda;aws lambda|serverless|2014
EC2|ec2|cloud|2006
S3|s3|cloud|2006
ECS|ecs|containers|2015
EKS|eks|k8s|2018
Fargate|fargate|containers|2017
CloudFormation|cloudformation|iac|2011
API Gateway|api gateway|cloud|2015
SQS|sqs|mq|2006
SNS|sns|mq|2010
Step Functions|step functions|serverless|2016
CloudWatch|cloudwatch|obs|2009
Azure Functions|azure functions|serverless|2016
AKS|aks|k8s|2018
GKE|gke|k8s|2015
Cloud Run|cloud run|serverless|2019
Heroku|heroku|paas|2007
Vercel|vercel|paas|2020
Netlify|netlify|paas|2014
Cloudflare Workers|cloudflare workers|serverless|2017
OpenShift|openshift|k8s|2011
Docker|docker|containers|2013
Kubernetes|kubernetes;k8s|k8s|2015
Helm|helm|k8s|2016
Terraform|terraform|iac|2014
Pulumi|pulumi|iac|2018
Ansible|ansible|iac|2012
Chef|Chef|iac|2009|c
Puppet|Puppet|iac|2005|c
Jenkins|jenkins|ci|2011
GitHub Actions|github actions|ci|2019
GitLab CI|gitlab ci;gitlab ci/cd|ci|2015
CircleCI|circleci|ci|2011
Travis CI|travis;travis ci|ci|2011
Argo CD|argo ?cd;argocd|ci|2018
CI/CD|ci/cd;continuous integration;continuous delivery|ci|2005|g
GitOps|gitops|ci|2017|g
Istio|istio|mesh|2017
Linkerd|linkerd|mesh|2016
Envoy|envoy|mesh|2016
NGINX|nginx|web-server|2004
Apache Kafka|kafka;apache kafka|mq|2011
Kafka Streams|kafka streams|stream|2016
RabbitMQ|rabbitmq|mq|2007
ActiveMQ|activemq|mq|2004
NATS|NATS|mq|2011|c
Apache Pulsar|pulsar|mq|2016
Kinesis|kinesis|stream|2013
Pub/Sub|pub/sub;pubsub|mq|2015
Prometheus|prometheus|obs|2012
Grafana|grafana|obs|2014
Datadog|datadog|obs|2010
New Relic|new relic|obs|2008
Splunk|splunk|obs|2003
ELK|elk;elk stack;kibana;logstash|obs|2012
OpenTelemetry|opentelemetry;otel|obs|2019
Jaeger|jaeger|obs|2016
Sentry|sentry|obs|2012
Vault|HashiCorp Vault;Vault|secrets|2015|c
Consul|consul|mesh|2014
Linux|linux|os|1991
Git|git|tools|2005
Apache Spark|spark;apache spark|bigdata|2014
PySpark|pyspark|bigdata|2014
Hadoop|hadoop|bigdata|2006
Hive|Hive|bigdata|2010|c
Airflow|airflow;apache airflow|orchestration|2015
dbt|dbt|transform|2016
Apache Flink|flink|stream|2014
Apache Beam|apache beam|stream|2016
Dagster|dagster|orchestration|2019
Prefect|prefect|orchestration|2018
Fivetran|fivetran|elt|2012
Pandas|pandas|pydata|2008
NumPy|numpy|pydata|2006
Polars|polars|pydata|2020
Tableau|tableau|bi|2003
Power BI|power bi|bi|2015
Looker|looker|bi|2012
ETL|etl;elt|data|2000|g
Data Warehousing|data warehouse;data warehousing|data|1995|g
Data Modeling|data model;data modeling;data modelling|data|1990|g
TensorFlow|tensorflow|ml-fw|2015
PyTorch|pytorch|ml-fw|2016
scikit-learn|scikit-learn;sklearn|ml-fw|2007
Keras|keras|ml-fw|2015
XGBoost|xgboost|ml-fw|2014
LightGBM|lightgbm|ml-fw|2016
Hugging Face|hugging ?face;transformers library|llm|2019
LangChain|langchain|llm|2022
LlamaIndex|llamaindex;llama index|llm|2022
LangGraph|langgraph|llm|2024
OpenAI API|openai;openai api;gpt-4o?;gpt-3\\.5|llm|2020
ChatGPT|chatgpt|llm|2022
Claude|Claude|llm|2023|c
Llama|Llama|llm|2023|c
LLMs|llm;llms;large language models?|llm|2020|g
RAG|rag;retrieval[- ]augmented generation|llm|2022|g
Generative AI|generative ai;genai;gen ai|llm|2022|g
Prompt Engineering|prompt engineering|llm|2021|g
AI Agents|ai agents?;agentic;llm agents?|llm|2023|g
Model Context Protocol|mcp;model context protocol|llm|2024
vLLM|vllm|llm|2023
Ollama|ollama|llm|2023
Amazon Bedrock|bedrock|llm|2023
Azure OpenAI|azure openai|llm|2023
Vertex AI|vertex ai|mlops|2021
SageMaker|sagemaker|mlops|2017
MLflow|mlflow|mlops|2018
Kubeflow|kubeflow|mlops|2018
Embeddings|embeddings?|llm|2013|g
Vector Search|vector search;semantic search;vector database|vector|2017|g
Fine-tuning|fine[- ]tuning|ml|2018|g
LoRA|lora|llm|2021
Machine Learning|machine learning|ml|1990|g
Deep Learning|deep learning|ml|2012|g
NLP|nlp;natural language processing|ml|1990|g
Computer Vision|computer vision|ml|1990|g
OpenCV|opencv|ml|2000
spaCy|spacy|ml|2015
Jupyter|jupyter|pydata|2014
Information Retrieval|information retrieval|search|1990|g
Relevance Ranking|relevance ranking;search relevance;learning to rank;ranking models?;relevance tuning|search|2000|g
Jest|jest|test|2014
Mocha|mocha|test|2011
Vitest|vitest|test|2021
Cypress|cypress|e2e|2015
Playwright|playwright|e2e|2020
Selenium|selenium|e2e|2004
Puppeteer|puppeteer|e2e|2017
pytest|pytest|test|2004
JUnit|junit|test|1998
React Testing Library|react testing library|test|2018
Postman|postman|test|2012
k6|k6|perf|2017
JMeter|jmeter|perf|1998
TDD|tdd;test[- ]driven|test|2000|g
iOS|ios|mobile|2008
Android|android|mobile|2008
React Native|react native|mobile-x|2015
Flutter|flutter|mobile-x|2017
SwiftUI|swiftui|mobile|2019
Jetpack Compose|jetpack compose|mobile|2021
Xamarin|xamarin|mobile-x|2011
Expo|Expo|mobile-x|2016|c
Ionic|ionic|mobile-x|2013
Microservices|microservices?|arch|2014|g
Distributed Systems|distributed systems?|arch|1990|g
System Design|system design|arch|1990|g
Event-Driven Architecture|event[- ]driven|arch|2005|g
Serverless|serverless|serverless|2015|g
Domain-Driven Design|ddd;domain[- ]driven design|arch|2004|g
CQRS|cqrs|arch|2010|g
Event Sourcing|event sourcing|arch|2005|g
Agile|agile|process|2001|g
Scrum|scrum|process|1995|g
Kanban|kanban|process|2007|g
Jira|jira|tools|2002
HIPAA|hipaa|compliance|1996|g
SOC 2|soc ?2|compliance|2010|g
PCI DSS|pci;pci dss|compliance|2004|g
GDPR|gdpr|compliance|2018|g
FHIR|fhir|health|2014
HL7|hl7|health|1990
OWASP|owasp|security|2001|g
`;

// families that count as "adjacent" to each other on top of same-family matches
const NEAR = { 'db-sql': ['orm'], 'db-nosql': ['cache'], search: ['vector'], vector: ['search', 'llm'], k8s: ['containers'], containers: ['k8s'],
  'node-web': ['node'], node: ['node-web'], ci: ['iac'], iac: ['ci'], mq: ['stream'], stream: ['mq'], dwh: ['transform', 'orchestration'],
  orchestration: ['dwh', 'bigdata'], bigdata: ['orchestration', 'pydata'], pydata: ['bigdata', 'ml-fw'], 'ml-fw': ['pydata', 'mlops'], mlops: ['ml-fw'],
  serverless: ['cloud'], cloud: ['serverless'], 'fe-fw': ['fe-meta', 'state'], 'fe-meta': ['fe-fw'], state: ['fe-fw'], css: ['fe-base'], 'fe-base': ['css'] };

const escRe = (s) => s;
export const TECH = DICT.trim().split('\n').map((line) => {
  const [name, aliases, family, year, flags = ''] = line.split('|');
  const alts = aliases.split(';').map(escRe).join('|');
  const re = new RegExp(`(?<![A-Za-z0-9+#.])(?:${alts})(?![A-Za-z0-9+#]|\\.[a-z])`, flags.includes('c') ? 'g' : 'gi');
  return { name, family, year: Number(year) || 0, generic: flags.includes('g'), re };
});
const BY_NAME = new Map(TECH.map((t) => [t.name.toLowerCase(), t]));
export const techByName = (n) => BY_NAME.get(String(n).toLowerCase()) || findTech(n)[0] || null;

// All technologies mentioned in a text: [{ tech, count }]
export function findTechCounts(text) {
  const s = String(text || '');
  const out = [];
  for (const t of TECH) {
    t.re.lastIndex = 0;
    const m = s.match(t.re);
    if (m) out.push({ tech: t, count: m.length });
  }
  // a longer name wins over the short one it contains (React Native over React, Spring Boot over Spring)
  const names = new Set(out.map((o) => o.tech.name));
  const contained = { React: 'React Native', Spring: 'Spring Boot', '.NET': '.NET Core', 'ASP.NET': 'ASP.NET Core', SQL: 'SQL Server', Kafka: 'Kafka Streams' };
  return out.filter((o) => {
    const longer = contained[o.tech.name];
    if (!longer || !names.has(longer)) return true;
    const rest = s.replace(techByName(longer).re, ' ');
    o.tech.re.lastIndex = 0;
    return o.tech.re.test(rest);
  });
}
export const findTech = (text) => findTechCounts(text).map((o) => o.tech);

export function adjacent(a, b) {
  if (a.name === b.name) return true;
  // general-purpose languages are not interchangeable (Java/Kotlin and JS/TS have their own families)
  if (a.family === 'lang' || b.family === 'lang') return false;
  if (a.family === b.family) return true;
  return (NEAR[a.family] || []).includes(b.family);
}

// ---------- job description ----------

const REQ_HEAD = /(requirements|qualifications|what you('ll)? (need|bring)|what we('re| are)? looking for|you have|you bring|must[- ]have|minimum|required|who you are|about you|skills)/i;
const PREF_HEAD = /(nice[- ]to[- ]have|preferred|bonus|plus|desired|good to have|extra credit)/i;
const STOP = new Set(('a an the and or of to in on for with you your we our us is are be will as at by from this that it its their they them our ' +
  'have has had can may must should would could not no all any each more most other some such than too very just also into over under ' +
  'about across within while who what which when where why how team work working role job company experience years year strong ability ' +
  'including include includes etc using use used new well build building help across join') .split(/\s+/));

// Splits the JD into lines tagged as requirement / preferred / other, by the nearest heading above.
function sections(jd) {
  let mode = 'other';
  return String(jd || '').split(/\n+/).map((line) => {
    const t = line.trim();
    if (t.length < 70 && PREF_HEAD.test(t)) mode = 'pref';
    else if (t.length < 70 && REQ_HEAD.test(t)) mode = 'req';
    return { line: t, mode };
  });
}

// Weighted keywords of a JD: technologies (from the dictionary) and repeated domain phrases.
export function jdKeywords(jd, title = '') {
  const tagged = sections(jd);
  const weights = new Map();
  for (const { line, mode } of tagged) {
    for (const { tech, count } of findTechCounts(line)) {
      const w = (mode === 'req' ? 2 : mode === 'pref' ? 0.8 : 1.2) + Math.min(count - 1, 2) * 0.4;
      const cur = weights.get(tech.name) || { tech, weight: 0, req: false, pref: false };
      cur.weight += w;
      cur.req ||= mode === 'req';
      cur.pref ||= mode === 'pref' && !cur.req;
      weights.set(tech.name, cur);
    }
  }
  for (const t of findTech(title)) {
    const cur = weights.get(t.name) || { tech: t, weight: 0, req: true };
    cur.weight += 2;
    weights.set(t.name, cur);
  }
  const tech = [...weights.values()].map((x) => ({ ...x, weight: Math.min(x.weight, 6) })).sort((a, b) => b.weight - a.weight);

  // two-word phrases used at least twice (domain language: "search relevance", "payment processing")
  const words = String(jd || '').toLowerCase().replace(/[^a-z0-9+#/ \n-]/g, ' ').split(/\s+/).filter(Boolean);
  const grams = new Map();
  for (let i = 0; i < words.length - 1; i++) {
    const [a, b] = [words[i], words[i + 1]];
    if (STOP.has(a) || STOP.has(b) || a.length < 3 || b.length < 3 || /^\d/.test(a) || /^\d/.test(b)) continue;
    const g = `${a} ${b}`;
    grams.set(g, (grams.get(g) || 0) + 1);
  }
  const techText = new Set(tech.map((t) => t.tech.name.toLowerCase()));
  const phrases = [...grams.entries()].filter(([g, n]) => n >= 2 && !findTech(g).some((t) => techText.has(t.name.toLowerCase())))
    .sort((a, b) => b[1] - a[1]).slice(0, 12).map(([g, n]) => ({ phrase: g, count: n }));

  const yrs = [...String(jd || '').matchAll(/(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?/gi)].map((m) => Number(m[1])).filter((n) => n > 0 && n < 30);
  return { tech, phrases, minYears: yrs.length ? Math.max(...yrs.filter((n) => n <= 15)) || 0 : 0 };
}

// ---------- resume ----------

export function resumeText(r) {
  const b = r?.basics || {};
  return [b.label, b.summary, ...(b.highlights || []).map((h) => `${h.name}: ${h.value}`),
    ...(r?.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`),
    ...(r?.work || []).flatMap((w) => [w.position, w.description, ...(w.highlights || [])])].filter(Boolean).join('\n');
}

const titleWords = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9+#/ ]/g, ' ').split(/\s+/).filter((w) => w.length > 1 && !STOP.has(w));

// ATS match 0-100 with what is matched and what is missing.
export function atsScore(resume, jd, jobTitle = '', totalYears = 0) {
  const k = jdKeywords(jd, jobTitle);
  const skillsText = (resume?.skills || []).map((s) => `${s.name}: ${(s.keywords || []).join(', ')}`).join('\n')
    + '\n' + (resume?.basics?.highlights || []).map((h) => h.value).join('\n');
  const contextText = [resume?.basics?.summary, ...(resume?.work || []).flatMap((w) => [w.description, ...(w.highlights || [])])].join('\n');
  const inSkills = new Set(findTech(skillsText).map((t) => t.name));
  const inContext = new Set(findTech(contextText).map((t) => t.name));

  let got = 0;
  let total = 0;
  const matched = [];
  const missing = [];
  const skillsOnly = [];
  for (const { tech, weight, req } of k.tech) {
    total += weight;
    const s = inSkills.has(tech.name);
    const c = inContext.has(tech.name);
    const part = s && c ? 1 : c ? 0.85 : s ? 0.7 : 0;
    got += weight * part;
    if (!part) missing.push({ name: tech.name, req });
    else {
      matched.push(tech.name);
      if (!c) skillsOnly.push(tech.name);
    }
  }
  const hard = total ? got / total : 1;

  const all = resumeText(resume).toLowerCase();
  const phraseHits = k.phrases.filter((p) => all.includes(p.phrase));
  const phrases = k.phrases.length ? phraseHits.length / k.phrases.length : 1;

  const tw = titleWords(jobTitle).filter((w) => !/^(i|ii|iii|iv|remote|us|usa|hybrid|onsite|contract|full|time|f\/t)$/.test(w));
  const label = String(resume?.basics?.label || '').toLowerCase();
  const title = tw.length ? tw.filter((w) => label.includes(w)).length / tw.length : 1;
  const years = !k.minYears || totalYears >= k.minYears ? 1 : totalYears / k.minYears;

  const score = Math.round(75 * hard + 10 * title + 10 * phrases + 5 * years);
  return {
    score: Math.max(0, Math.min(100, score)),
    parts: { keywords: Math.round(hard * 100), title: Math.round(title * 100), phrases: Math.round(phrases * 100), years: Math.round(years * 100) },
    matched, missing, skillsOnly,
    missingPhrases: k.phrases.filter((p) => !phraseHits.includes(p)).map((p) => p.phrase),
    jdYears: k.minYears, keywords: k
  };
}

// Overlap between a profile and a job, for the "Match %" on the job list. jobTech = names from jobs.js.
const famCache = new WeakMap();
function familiesOf(set) {
  if (!famCache.has(set)) {
    const fams = new Set();
    for (const n of set) {
      const t = techByName(n);
      if (!t || t.generic || t.family === 'lang') continue;
      fams.add(t.family);
      for (const f of NEAR[t.family] || []) fams.add(f);
    }
    famCache.set(set, fams);
  }
  return famCache.get(set);
}

export function quickMatch(profileTech, jobTech) {
  if (!jobTech?.length || !profileTech?.size) return 0;
  const fams = familiesOf(profileTech);
  let hit = 0;
  for (const n of jobTech) {
    if (profileTech.has(n)) hit += 1;
    else if (fams.has(techByName(n)?.family)) hit += 0.4;
  }
  return Math.round((hit / jobTech.length) * 100);
}

export function profileTechSet(resume) {
  const names = new Set();
  for (const t of findTech(resumeText(resume))) names.add(t.name);
  for (const w of resume?.work || []) for (const s of w.stack || []) for (const t of findTech(s)) names.add(t.name);
  return names;
}
