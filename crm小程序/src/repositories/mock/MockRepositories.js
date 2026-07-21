class BaseRepository {
  constructor(store, collection) {
    this.store = store;
    this.collection = collection;
  }
  async list(filter = {}) {
    return this.store
      .list(this.collection)
      .filter((item) =>
        Object.entries(filter).every(
          ([key, value]) => !value || item[key] === value,
        ),
      );
  }
  async getById(id) {
    return this.store.get(this.collection, id);
  }
  async create(item) {
    return this.store.create(this.collection, item);
  }
  async update(item) {
    return this.store.update(this.collection, item.id, item);
  }
}

export class MockCustomerRepository extends BaseRepository {
  constructor(store) {
    super(store, "customers");
  }
  async findDuplicate({ name, phone, excludeId = "" }) {
    return (
      (await this.list()).find(
        (item) =>
          item.id !== excludeId &&
          (item.name.trim() === name?.trim() ||
            (phone &&
              item.contacts?.some((contact) => contact.phone === phone))),
      ) || null
    );
  }
  async delete(id) {
    const saleIds = new Set(
      this.store
        .list("sales")
        .filter((item) => item.customerId === id)
        .map((item) => item.id),
    );
    this.store.removeWhere("visits", (item) => item.customerId === id);
    this.store.removeWhere("opportunities", (item) => item.customerId === id);
    this.store.removeWhere("sales", (item) => item.customerId === id);
    this.store.removeWhere("erpSyncRecords", (item) => saleIds.has(item.saleId));
    const removed = this.store.remove(this.collection, id);
    this.store.removeWhere("auditLogs", (item) => item.customerId === id);
    return removed;
  }
}
export class MockVisitRepository extends BaseRepository {
  constructor(store) {
    super(store, "visits");
  }
}
export class MockOpportunityRepository extends BaseRepository {
  constructor(store) {
    super(store, "opportunities");
  }
}
export class MockSalesRepository extends BaseRepository {
  constructor(store) {
    super(store, "sales");
  }
}
export class MockErpSyncRepository extends BaseRepository {
  constructor(store) {
    super(store, "erpSyncRecords");
  }
  async findBySaleId(saleId) {
    return (await this.list({ saleId }))[0] || null;
  }
}
export class MockAuditRepository extends BaseRepository {
  constructor(store) {
    super(store, "auditLogs");
  }
}
