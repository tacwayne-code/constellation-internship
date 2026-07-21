import { createContact, createCustomer } from "../domain/models.js";
import { ErpSyncStatus, SaleStatus } from "../domain/status.js";
import {
  assertCustomerAccess,
  canAccessAllCustomers,
  createContext,
  writeAudit,
} from "./serviceSupport.js";

export class CustomerService {
  constructor({ repositories, store }) {
    this.repositories = repositories;
    this.store = store;
  }
  async listCustomers(actor) {
    const rows = await this.repositories.customer.list();
    return canAccessAllCustomers(actor)
      ? rows
      : rows.filter((row) => row.ownerId === actor.id);
  }
  async getCustomerDetail(id, actor) {
    const customer = assertCustomerAccess(
      await this.repositories.customer.getById(id),
      actor,
    );
    const [visits, opportunities, sales, erpSyncRecords, timeline] =
      await Promise.all([
        this.repositories.visit.list({ customerId: id }),
        this.repositories.opportunity.list({ customerId: id }),
        this.repositories.sale.list({ customerId: id }),
        this.repositories.erpSync.list(),
        this.repositories.audit.list({ customerId: id }),
      ]);
    const saleIds = new Set(sales.map((row) => row.id));
    return {
      customer,
      visits,
      opportunities,
      sales,
      erpSyncRecords: erpSyncRecords.filter((row) => saleIds.has(row.saleId)),
      timeline,
    };
  }
  async checkDuplicateCustomer(input, actor) {
    const duplicate = await this.repositories.customer.findDuplicate({
      name: input.name,
      phone: input.phone || input.contacts?.[0]?.phone,
      excludeId: input.id,
    });
    if (!duplicate) return null;
    assertCustomerAccess(duplicate, actor);
    return duplicate;
  }
  async createCustomer(input, actor) {
    if (await this.checkDuplicateCustomer(input, actor))
      throw new Error("客户名称或联系电话已存在");
    const contacts =
      input.contacts ||
      (input.contact
        ? [
            createContact({
              name: input.contact,
              phone: input.phone,
              isPrimary: true,
            }),
          ]
        : []);
    const customer = createCustomer(
      {
        ...input,
        contacts,
        ownerId: input.ownerId || actor.id,
        ownerName: input.ownerName || actor.name,
      },
      createContext(this.store, actor),
    );
    const saved = await this.repositories.customer.create(customer);
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.id,
      entityType: "CUSTOMER",
      entityId: saved.id,
      action: "CREATED",
      detail: "创建客户档案",
    });
    return saved;
  }
  async updateCustomer(input, actor) {
    assertCustomerAccess(
      await this.repositories.customer.getById(input.id),
      actor,
    );
    if (await this.checkDuplicateCustomer(input, actor))
      throw new Error("客户名称或联系电话已存在");
    const saved = await this.repositories.customer.update({
      ...input,
      updatedAt: new Date().toISOString(),
      updatedBy: actor.id,
    });
    await writeAudit(this.repositories, this.store, actor, {
      customerId: saved.id,
      entityType: "CUSTOMER",
      entityId: saved.id,
      action: "UPDATED",
      detail: "更新客户资料",
    });
    return saved;
  }
  async deleteCustomer(id, actor) {
    const customer = assertCustomerAccess(
      await this.repositories.customer.getById(id),
      actor,
    );
    const [visits, opportunities, sales, erpSyncRecords] = await Promise.all([
      this.repositories.visit.list({ customerId: id }),
      this.repositories.opportunity.list({ customerId: id }),
      this.repositories.sale.list({ customerId: id }),
      this.repositories.erpSync.list(),
    ]);
    const saleIds = new Set(sales.map((sale) => sale.id)),
      relatedSyncRecords = erpSyncRecords.filter((record) =>
        saleIds.has(record.saleId),
      ),
      hasErpBusiness =
        customer.erpCustomerId ||
        customer.erpCustomerCode ||
        sales.some(
          (sale) =>
            sale.erpOrderId ||
            sale.erpOrderNo ||
            [
              SaleStatus.ERP_PENDING,
              SaleStatus.ERP_SYNCING,
              SaleStatus.ERP_SUCCESS,
            ].includes(sale.status) ||
            [
              ErpSyncStatus.PENDING,
              ErpSyncStatus.SYNCING,
              ErpSyncStatus.SUCCESS,
            ].includes(sale.erpSyncStatus),
        ) ||
        relatedSyncRecords.some(
          (record) =>
            record.erpOrderId ||
            record.erpOrderNo ||
            [
              ErpSyncStatus.PENDING,
              ErpSyncStatus.SYNCING,
              ErpSyncStatus.SUCCESS,
            ].includes(record.status),
        );
    if (hasErpBusiness)
      throw new Error("该客户或销售已经进入Odoo同步流程，不能级联删除");
    return this.repositories.customer.delete(id);
  }
}
