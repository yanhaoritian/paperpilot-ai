const { ChatOpenAI, OpenAIEmbeddings } = require("@langchain/openai");
const { HumanMessage, SystemMessage } = require("@langchain/core/messages");

function requireApiKey(apiKey, purpose) {
  if (!apiKey) {
    const err = new Error(purpose || "缺少 OPENAI_API_KEY。");
    err.code = "MISSING_OPENAI_API_KEY";
    throw err;
  }
}

function toLangChainMessages(messages) {
  const out = [];
  for (const m of Array.isArray(messages) ? messages : []) {
    const role = String(m?.role || "").trim();
    const content = String(m?.content || "");
    if (!content) continue;
    if (role === "system") out.push(new SystemMessage(content));
    else out.push(new HumanMessage(content));
  }
  return out;
}

function createChatModel({ apiKey, baseURL, model, temperature }) {
  requireApiKey(apiKey, "总结功能暂时不可用，请稍后再试。");
  return new ChatOpenAI({
    apiKey,
    configuration: baseURL ? { baseURL } : undefined,
    model: model || undefined,
    temperature: Number.isFinite(Number(temperature)) ? Number(temperature) : 0.3
  });
}

function createEmbeddings({ apiKey, baseURL, model, batchSize }) {
  requireApiKey(apiKey, "缺少 OPENAI_API_KEY，无法执行向量检索。");
  return new OpenAIEmbeddings({
    apiKey,
    configuration: baseURL ? { baseURL } : undefined,
    model: model || undefined,
    batchSize: Number.isFinite(Number(batchSize)) ? Number(batchSize) : undefined
  });
}

async function chatJson({ apiKey, baseURL, model, temperature, messages }) {
  const llm = createChatModel({ apiKey, baseURL, model, temperature });
  const lcMessages = toLangChainMessages(messages);
  const res = await llm.invoke(lcMessages, {
    response_format: { type: "json_object" }
  });
  return String(res?.content || "");
}

module.exports = {
  createChatModel,
  createEmbeddings,
  toLangChainMessages,
  chatJson
};

