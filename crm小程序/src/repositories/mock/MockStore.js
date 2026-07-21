import { createBusinessNumberGenerator } from "../../domain/businessNumber.js";

const clone = (value) => (value == null ? value : structuredClone(value));

export class MockStore {
  constructor({ seed = {}, storage = null, storageKey = "crm-mock-v2" } = {}) {
    this.seed = clone(seed);
    this.storage = storage;
    this.storageKey = storageKey;
    const saved = storage?.getItem(storageKey);
    this.state = saved ? JSON.parse(saved) : this.normalize(seed);
  }

  normalize(seed) {
    return {
      customers: [],
      visits: [],
      opportunities: [],
      sales: [],
      erpSyncRecords: [],
      auditLogs: [],
      counters: {},
      ...clone(seed),
    };
  }

  persist() {
    this.storage?.setItem(this.storageKey, JSON.stringify(this.state));
  }
  list(collection) {
    return clone(this.state[collection] || []);
  }
  get(collection, id) {
    return clone(
      (this.state[collection] || []).find((item) => item.id === id) || null,
    );
  }
  create(collection, item) {
    if (this.get(collection, item.id)) throw new Error(`${item.id} 已存在`);
    this.state[collection].unshift(clone(item));
    this.persist();
    return clone(item);
  }
  update(collection, id, patch) {
    const index = this.state[collection].findIndex((item) => item.id === id);
    if (index < 0) throw new Error(`未找到记录：${id}`);
    this.state[collection][index] = {
      ...this.state[collection][index],
      ...clone(patch),
    };
    this.persist();
    return clone(this.state[collection][index]);
  }
  remove(collection, id) {
    const index = this.state[collection].findIndex((item) => item.id === id);
    if (index < 0) throw new Error(`未找到记录：${id}`);
    const [removed] = this.state[collection].splice(index, 1);
    this.persist();
    return clone(removed);
  }
  removeWhere(collection, predicate) {
    const before = this.state[collection].length;
    this.state[collection] = this.state[collection].filter(
      (item) => !predicate(item),
    );
    if (this.state[collection].length !== before) this.persist();
  }
  nextId(type, date = new Date()) {
    const generator = createBusinessNumberGenerator(this.state.counters);
    const id = generator.next(type, date);
    this.state.counters = generator.snapshot();
    this.persist();
    return id;
  }
  reset() {
    this.state = this.normalize(this.seed);
    this.persist();
    return this.snapshot();
  }
  snapshot() {
    return clone(this.state);
  }
}
