export class ApiErpAdapter {
  constructor(client) {
    this.client = client;
  }

  async searchProducts(query, { limit = 12 } = {}) {
    const params = new URLSearchParams({
      q: String(query || "").trim(),
      limit: String(limit),
    });
    const response = await this.client.request(`/erp/products?${params}`);
    return response.items || [];
  }

  async submitSale(payload, { idempotencyKey }) {
    const response = await this.client.request(
      `/erp/sales/${encodeURIComponent(payload.id)}/submit`,
      {
        method: "POST",
        body: { idempotencyKey },
      },
    );
    return response.result;
  }
}
