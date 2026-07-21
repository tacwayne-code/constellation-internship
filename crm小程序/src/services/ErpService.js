import { createErpSyncRecord } from "../domain/models.js";
import {
  CustomerErpSyncStatus,
  ErpSyncStatus,
  SaleStatus,
} from "../domain/status.js";
import { assertSaleTransition } from "../domain/stateMachines.js";
import { createContext, writeAudit } from "./serviceSupport.js";

export class ErpService {
  constructor({ repositories, store, erpAdapter, salesService }) {
    this.repositories = repositories;
    this.store = store;
    this.erpAdapter = erpAdapter;
    this.salesService = salesService;
  }
  async searchProducts(query) {
    const keyword = String(query || "").trim();
    if (!keyword) return [];
    return this.erpAdapter.searchProducts(keyword, { limit: 12 });
  }
  async submitSaleToErp(id, actor) {
    const { sale, customer } = await this.salesService.getSaleDetail(id, actor);
    const existing = await this.repositories.erpSync.findBySaleId(id);
    if (sale.status === SaleStatus.ERP_SUCCESS && existing) return existing;
    if (![SaleStatus.CONFIRMED, SaleStatus.ERP_PENDING].includes(sale.status))
      throw new Error("只有已确认的实际销售才能提交ERP");
    if (sale.status === SaleStatus.CONFIRMED) {
      assertSaleTransition(sale.status, SaleStatus.ERP_PENDING);
      await this.repositories.sale.update({
        ...sale,
        status: SaleStatus.ERP_PENDING,
        erpSyncStatus: ErpSyncStatus.PENDING,
      });
    }
    const pendingSale = await this.repositories.sale.getById(id);
    assertSaleTransition(pendingSale.status, SaleStatus.ERP_SYNCING);
    await this.repositories.sale.update({
      ...pendingSale,
      status: SaleStatus.ERP_SYNCING,
      erpSyncStatus: ErpSyncStatus.SYNCING,
    });
    let record = existing;
    if (!record)
      record = await this.repositories.erpSync.create(
        createErpSyncRecord(
          { saleId: id, idempotencyKey: id, requestPayload: pendingSale },
          createContext(this.store, actor),
        ),
      );
    record = await this.repositories.erpSync.update({
      ...record,
      status: ErpSyncStatus.SYNCING,
      attemptCount: record.attemptCount + 1,
      errorMessage: "",
    });
    try {
      const result = await this.erpAdapter.submitSale(pendingSale, {
        idempotencyKey: id,
      });
      const syncedAt = new Date().toISOString();
      if (result.erpCustomerId) {
        await this.repositories.customer.update({
          ...customer,
          erpCustomerId: result.erpCustomerId,
          erpCustomerCode:
            result.erpCustomerCode || customer.erpCustomerCode || customer.id,
          erpSyncStatus: CustomerErpSyncStatus.SYNCED,
          updatedAt: syncedAt,
          updatedBy: actor.id,
        });
      }
      await this.repositories.sale.update({
        ...pendingSale,
        status: SaleStatus.ERP_SUCCESS,
        erpSyncStatus: ErpSyncStatus.SUCCESS,
        erpOrderId: result.erpOrderId,
        erpOrderNo: result.erpOrderNo,
        erpOrderStatus: result.erpOrderStatus,
        erpErrorMessage: "",
        erpSyncedAt: syncedAt,
      });
      record = await this.repositories.erpSync.update({
        ...record,
        status: ErpSyncStatus.SUCCESS,
        responsePayload: result,
        erpOrderId: result.erpOrderId,
        erpOrderNo: result.erpOrderNo,
        erpOrderStatus: result.erpOrderStatus,
        updatedAt: syncedAt,
      });
      await writeAudit(this.repositories, this.store, actor, {
        customerId: sale.customerId,
        entityType: "ERP_SYNC",
        entityId: record.id,
        action: "ERP_SUCCESS",
        fromStatus: SaleStatus.ERP_SYNCING,
        toStatus: SaleStatus.ERP_SUCCESS,
        detail: `ERP返回订单号 ${result.erpOrderNo}`,
      });
      return record;
    } catch (error) {
      await this.repositories.sale.update({
        ...pendingSale,
        status: SaleStatus.ERP_FAILED,
        erpSyncStatus: ErpSyncStatus.FAILED,
        erpErrorMessage: error.message,
      });
      record = await this.repositories.erpSync.update({
        ...record,
        status: ErpSyncStatus.FAILED,
        errorMessage: error.message,
        updatedAt: new Date().toISOString(),
      });
      await writeAudit(this.repositories, this.store, actor, {
        customerId: sale.customerId,
        entityType: "ERP_SYNC",
        entityId: record.id,
        action: "ERP_FAILED",
        fromStatus: SaleStatus.ERP_SYNCING,
        toStatus: SaleStatus.ERP_FAILED,
        detail: error.message,
      });
      return record;
    }
  }
  async retryErpSync(id, actor) {
    const { sale } = await this.salesService.getSaleDetail(id, actor);
    if (sale.status !== SaleStatus.ERP_FAILED)
      throw new Error("只有ERP同步失败的销售才能重试");
    assertSaleTransition(sale.status, SaleStatus.ERP_PENDING);
    await this.repositories.sale.update({
      ...sale,
      status: SaleStatus.ERP_PENDING,
      erpSyncStatus: ErpSyncStatus.PENDING,
    });
    return this.submitSaleToErp(id, actor);
  }
  async getErpSyncResult(id, actor) {
    return (await this.salesService.getSaleDetail(id, actor)).erpSyncRecord;
  }
}
