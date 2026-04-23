const fs = require("fs/promises");
const path = require("path");
const { BaseCheckpointSaver, WRITES_IDX_MAP } = require("@langchain/langgraph");

function ensureDir(p) {
  return fs.mkdir(p, { recursive: true });
}

function nowIso() {
  return new Date().toISOString();
}

function generateKey(threadId, checkpointNamespace, checkpointId) {
  return JSON.stringify([threadId, checkpointNamespace, checkpointId]);
}

function parseKey(key) {
  const [threadId, checkpointNamespace, checkpointId] = JSON.parse(key);
  return { threadId, checkpointNamespace, checkpointId };
}

class JsonFileSaver extends BaseCheckpointSaver {
  /**
   * @param {{ filePath: string, flushDebounceMs?: number }} options
   */
  constructor(options = {}) {
    super();
    this.filePath =
      options.filePath || path.resolve(__dirname, "..", "data", "langgraph-checkpoints.json");
    this.flushDebounceMs = Number.isFinite(Number(options.flushDebounceMs))
      ? Number(options.flushDebounceMs)
      : 300;
    this._loaded = false;
    this._storage = {};
    this._writes = {};
    this._flushTimer = null;
    this._flushPromise = null;
    this._dirty = false;
    this._lastLoadedAt = null;
  }

  async _loadOnce() {
    if (this._loaded) return;
    this._loaded = true;
    try {
      const raw = await fs.readFile(this.filePath, "utf8");
      const parsed = JSON.parse(raw);
      this._storage = parsed?.storage && typeof parsed.storage === "object" ? parsed.storage : {};
      this._writes = parsed?.writes && typeof parsed.writes === "object" ? parsed.writes : {};
      this._lastLoadedAt = nowIso();
    } catch (err) {
      if (err && err.code === "ENOENT") {
        this._storage = {};
        this._writes = {};
        this._lastLoadedAt = nowIso();
        return;
      }
      throw err;
    }
  }

  _scheduleFlush() {
    this._dirty = true;
    if (this._flushTimer) return;
    this._flushTimer = setTimeout(() => {
      this._flushTimer = null;
      this._flushPromise = this._flushNow().catch(() => {});
    }, this.flushDebounceMs);
  }

  async _flushNow() {
    if (!this._dirty) return;
    this._dirty = false;
    const dir = path.dirname(this.filePath);
    await ensureDir(dir);
    const tmp = `${this.filePath}.tmp`;
    const payload = JSON.stringify(
      { v: 1, updated_at: nowIso(), storage: this._storage, writes: this._writes },
      null,
      2
    );
    await fs.writeFile(tmp, payload, "utf8");
    await fs.rename(tmp, this.filePath);
  }

  async getTuple(config) {
    await this._loadOnce();
    const threadId = config.configurable?.thread_id;
    const checkpointNs = config.configurable?.checkpoint_ns ?? "";
    let checkpointId = config.configurable?.checkpoint_id || config.configurable?.thread_ts || "";

    if (!threadId) return;

    if (!checkpointId) {
      const checkpoints = this._storage[threadId]?.[checkpointNs];
      if (!checkpoints) return;
      checkpointId = Object.keys(checkpoints).sort((a, b) => b.localeCompare(a))[0];
    }

    const saved = this._storage[threadId]?.[checkpointNs]?.[checkpointId];
    if (!saved) return;
    const [checkpointStr, metadataStr, parentCheckpointId] = saved;
    const key = generateKey(threadId, checkpointNs, checkpointId);
    const checkpoint = await this.serde.loadsTyped("json", checkpointStr);
    const metadata = await this.serde.loadsTyped("json", metadataStr);
    const pendingWrites = await Promise.all(
      Object.values(this._writes[key] || {}).map(async ([taskId, channel, value]) => {
        return [taskId, channel, await this.serde.loadsTyped("json", value)];
      })
    );

    const tuple = { config, checkpoint, metadata, pendingWrites };
    if (parentCheckpointId !== void 0) {
      tuple.parentConfig = {
        configurable: { thread_id: threadId, checkpoint_ns: checkpointNs, checkpoint_id: parentCheckpointId }
      };
    }
    return tuple;
  }

  async *list(config, options) {
    await this._loadOnce();
    let { before, limit, filter } = options ?? {};
    const threadIds = config.configurable?.thread_id ? [config.configurable.thread_id] : Object.keys(this._storage);
    const configCheckpointNamespace = config.configurable?.checkpoint_ns;
    const configCheckpointId = config.configurable?.checkpoint_id;

    for (const threadId of threadIds) {
      for (const checkpointNs of Object.keys(this._storage[threadId] ?? {})) {
        if (configCheckpointNamespace !== void 0 && checkpointNs !== configCheckpointNamespace) continue;
        const checkpoints = this._storage[threadId]?.[checkpointNs] ?? {};
        const sorted = Object.entries(checkpoints).sort((a, b) => b[0].localeCompare(a[0]));
        for (const [checkpointId, [checkpointStr, metadataStr, parentCheckpointId]] of sorted) {
          if (configCheckpointId && checkpointId !== configCheckpointId) continue;
          if (before && before.configurable?.checkpoint_id && checkpointId >= before.configurable.checkpoint_id) continue;
          const metadata = await this.serde.loadsTyped("json", metadataStr);
          if (filter && !Object.entries(filter).every(([k, v]) => metadata[k] === v)) continue;
          if (limit !== void 0) {
            if (limit <= 0) break;
            limit -= 1;
          }
          const key = generateKey(threadId, checkpointNs, checkpointId);
          const pendingWrites = await Promise.all(
            Object.values(this._writes[key] || {}).map(async ([taskId, channel, value]) => {
              return [taskId, channel, await this.serde.loadsTyped("json", value)];
            })
          );
          const checkpoint = await this.serde.loadsTyped("json", checkpointStr);
          const tuple = {
            config: { configurable: { thread_id: threadId, checkpoint_ns: checkpointNs, checkpoint_id: checkpointId } },
            checkpoint,
            metadata,
            pendingWrites
          };
          if (parentCheckpointId !== void 0) {
            tuple.parentConfig = {
              configurable: { thread_id: threadId, checkpoint_ns: checkpointNs, checkpoint_id: parentCheckpointId }
            };
          }
          yield tuple;
        }
      }
    }
  }

  async put(config, checkpoint, metadata) {
    await this._loadOnce();
    const threadId = config.configurable?.thread_id;
    const checkpointNs = config.configurable?.checkpoint_ns ?? "";
    if (threadId === void 0) {
      throw new Error('Failed to put checkpoint. Missing "thread_id" in config.configurable.');
    }
    if (!this._storage[threadId]) this._storage[threadId] = {};
    if (!this._storage[threadId][checkpointNs]) this._storage[threadId][checkpointNs] = {};
    const [[, checkpointStr], [, metadataStr]] = await Promise.all([
      this.serde.dumpsTyped(checkpoint),
      this.serde.dumpsTyped(metadata)
    ]);
    this._storage[threadId][checkpointNs][checkpoint.id] = [checkpointStr, metadataStr, config.configurable?.checkpoint_id];
    this._scheduleFlush();
    return { configurable: { thread_id: threadId, checkpoint_ns: checkpointNs, checkpoint_id: checkpoint.id } };
  }

  async putWrites(config, writes, taskId) {
    await this._loadOnce();
    const threadId = config.configurable?.thread_id;
    const checkpointNs = config.configurable?.checkpoint_ns ?? "";
    const checkpointId = config.configurable?.checkpoint_id;
    if (threadId === void 0) throw new Error('Failed to put writes. Missing "thread_id" in config.configurable.');
    if (checkpointId === void 0) throw new Error('Failed to put writes. Missing "checkpoint_id" in config.configurable.');
    const outerKey = generateKey(threadId, checkpointNs, checkpointId);
    const outerWrites = this._writes[outerKey];
    if (this._writes[outerKey] === void 0) this._writes[outerKey] = {};

    await Promise.all(
      writes.map(async ([channel, value], idx) => {
        const [, valueStr] = await this.serde.dumpsTyped(value);
        const innerKey = [taskId, WRITES_IDX_MAP[channel] || idx];
        const innerKeyStr = `${innerKey[0]},${innerKey[1]}`;
        if (innerKey[1] >= 0 && outerWrites && innerKeyStr in outerWrites) return;
        this._writes[outerKey][innerKeyStr] = [taskId, channel, valueStr];
      })
    );

    this._scheduleFlush();
  }

  async deleteThread(threadId) {
    await this._loadOnce();
    delete this._storage[threadId];
    for (const key of Object.keys(this._writes)) {
      if (parseKey(key).threadId === threadId) delete this._writes[key];
    }
    this._scheduleFlush();
  }
}

module.exports = { JsonFileSaver };

