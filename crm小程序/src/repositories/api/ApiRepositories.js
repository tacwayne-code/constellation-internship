class ApiBaseRepository {
  constructor(client, resource) {
    this.client = client;
    this.resource = resource;
  }

  async list(filter = {}) {
    const query = new URLSearchParams(
      Object.entries(filter).filter(
        ([, value]) => value !== undefined && value !== null && value !== "",
      ),
    ).toString();
    const payload = await this.client.request(
      `/${this.resource}${query ? `?${query}` : ""}`,
    );
    return payload.items;
  }

  async getById(id) {
    try {
      return (
        await this.client.request(`/${this.resource}/${encodeURIComponent(id)}`)
      ).item;
    } catch (error) {
      if (error.status === 404) return null;
      throw error;
    }
  }

  async create(item) {
    return (
      await this.client.request(`/${this.resource}`, {
        method: "POST",
        body: item,
      })
    ).item;
  }

  async update(item) {
    return (
      await this.client.request(
        `/${this.resource}/${encodeURIComponent(item.id)}`,
        { method: "PUT", body: item },
      )
    ).item;
  }
}

export class ApiCustomerRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "customers");
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
    return (
      await this.client.request(
        `/${this.resource}/${encodeURIComponent(id)}`,
        { method: "DELETE" },
      )
    ).item;
  }
}

export class ApiVisitRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "visits");
  }
}

export class ApiOpportunityRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "opportunities");
  }
}

export class ApiSalesRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "sales");
  }
}

export class ApiErpSyncRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "erp-sync-records");
  }

  async findBySaleId(saleId) {
    return (await this.list({ saleId }))[0] || null;
  }
}

export class ApiAuditRepository extends ApiBaseRepository {
  constructor(client) {
    super(client, "audit-logs");
  }
}
