const crypto = require("crypto");
const { Annotation, StateGraph, END } = require("@langchain/langgraph");
const { MemorySaver } = require("@langchain/langgraph");
const { JsonFileSaver } = require("./checkpointer");
const { createEmbeddings, chatJson } = require("../langchain/openai");

function sha1(text) {
  return crypto.createHash("sha1").update(String(text || ""), "utf8").digest("hex");
}

function defaultThreadId({ scope, documentId, question, history }) {
  const base = JSON.stringify({
    scope: scope || "library",
    documentId: documentId || "",
    question: String(question || "").slice(0, 800),
    history_len: Array.isArray(history) ? history.length : 0
  });
  return sha1(base);
}

function parseMaybeJson(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function buildNoHitsResult() {
  return {
    answer:
      "未在文献中检索到与该问题足够相关的片段，暂无法基于原文可靠作答。建议把问题写得更具体（例如指定方法名、数据集或实验设置），或围绕论文的研究问题 / 方法 / 结论改写提问。",
    citations: [],
    confidence: "N/A",
    out_of_scope: false
  };
}

function compileRagGraph({ env, helpers, useCheckpoint }) {
  const State = Annotation.Root({
    scope: Annotation(),
    documentId: Annotation(),
    question: Annotation(),
    history: Annotation({ default: () => [] }),
    model: Annotation(),
    temperature: Annotation(),
    topK: Annotation(),
    intent: Annotation(),
    expandedQuestions: Annotation({ default: () => [] }),
    qVectors: Annotation({ default: () => [] }),
    retrieved: Annotation({ default: () => [] }),
    degraded: Annotation({ default: () => false }),
    retrievalMeta: Annotation({ default: () => ({}) }),
    messages: Annotation({ default: () => [] }),
    rawModelText: Annotation(),
    rawModelJson: Annotation(),
    result: Annotation(),
    allowed: Annotation({ default: () => ({}) })
  });

  const inferIntent = async (state) => {
    const intent = helpers.inferQuestionIntent(state.question);
    const expandedQuestions = helpers.buildExpandedQuestions(state.question, intent);
    return { intent, expandedQuestions };
  };

  const embedQuery = async (state) => {
    const embeddings = createEmbeddings({
      apiKey: env.apiKey,
      baseURL: env.baseURL,
      model: env.embeddingModel,
      batchSize: env.embedBatchSize
    });
    const vectors = await embeddings.embedDocuments(state.expandedQuestions);
    return { qVectors: vectors };
  };

  const retrieve = async (state) => {
    const topK = Number.isFinite(Number(state.topK)) ? Number(state.topK) : env.ragTopK;
    const out = await helpers.retrieve(state, { topK });
    return out;
  };

  const ensureProfiles = async (state) => {
    if (state.scope !== "library") return {};
    if (typeof helpers.ensureProfiles !== "function") return {};
    await helpers.ensureProfiles(state.retrieved);
    return {};
  };

  const buildMessages = async (state) => {
    const messages = await helpers.buildMessages(state);
    return { messages };
  };

  const generate = async (state) => {
    const rawText = await chatJson({
      apiKey: env.apiKey,
      baseURL: env.baseURL,
      model: state.model || env.defaultModel,
      temperature: state.temperature,
      messages: state.messages
    });
    const rawModelJson = parseMaybeJson(rawText);
    return { rawModelText: rawText, rawModelJson };
  };

  const validateCitations = async (state) => {
    const out = await helpers.validateAndSanitize(state);
    return out;
  };

  const persistHistory = async (state) => {
    if (typeof helpers.persistHistory !== "function") return {};
    await helpers.persistHistory(state);
    return {};
  };

  const fallbackNoHits = async (state) => {
    const noHits = typeof helpers.noHitsResult === "function" ? helpers.noHitsResult(state) : buildNoHitsResult();
    return {
      degraded: true,
      result: noHits,
      retrievalMeta: { hit_count: 0 }
    };
  };

  const graph = new StateGraph(State)
    .addNode("inferIntent", inferIntent)
    .addNode("embedQuery", embedQuery)
    .addNode("retrieve", retrieve)
    .addNode("ensureProfiles", ensureProfiles)
    .addNode("buildMessages", buildMessages)
    .addNode("generate", generate)
    .addNode("validateCitations", validateCitations)
    .addNode("persistHistory", persistHistory)
    .addNode("fallbackNoHits", fallbackNoHits)
    .addEdge("__start__", "inferIntent")
    .addEdge("inferIntent", "embedQuery")
    .addEdge("embedQuery", "retrieve")
    .addConditionalEdges(
      "retrieve",
      (state) => (Array.isArray(state.retrieved) && state.retrieved.length > 0 ? "hasHits" : "noHits"),
      { hasHits: "ensureProfiles", noHits: "fallbackNoHits" }
    )
    .addEdge("fallbackNoHits", END)
    .addEdge("ensureProfiles", "buildMessages")
    .addEdge("buildMessages", "generate")
    .addEdge("generate", "validateCitations")
    .addEdge("validateCitations", "persistHistory")
    .addEdge("persistHistory", END);

  const checkpointer = useCheckpoint
    ? new JsonFileSaver({ filePath: env.checkpointFilePath })
    : new MemorySaver();

  return graph.compile({ checkpointer });
}

async function runRagGraph(compiled, input, { threadId, checkpointNs } = {}) {
  const tid = threadId || defaultThreadId(input);
  const config = { configurable: { thread_id: tid, checkpoint_ns: checkpointNs || "rag" } };
  return await compiled.invoke(input, config);
}

module.exports = { compileRagGraph, runRagGraph, defaultThreadId };

