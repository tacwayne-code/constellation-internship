import { MockErpAdapter } from "../adapters/MockErpAdapter.js";
import { mockSeed } from "../mocks/seed.js";
import { assertRepositoryContract } from "../repositories/contracts.js";
import {
  MockAuditRepository,
  MockCustomerRepository,
  MockErpSyncRepository,
  MockOpportunityRepository,
  MockSalesRepository,
  MockVisitRepository,
} from "../repositories/mock/MockRepositories.js";
import { MockStore } from "../repositories/mock/MockStore.js";
import { CustomerService } from "../services/CustomerService.js";
import { ErpService } from "../services/ErpService.js";
import { OpportunityService } from "../services/OpportunityService.js";
import { SalesService } from "../services/SalesService.js";
import { VisitService } from "../services/VisitService.js";

export function createMockServiceRegistry(options = {}) {
  const store = new MockStore({
    seed: options.seed || mockSeed,
    storage: options.storage ?? null,
    storageKey: options.storageKey,
  });
  const repositories = {
    customer: assertRepositoryContract(
      "customer",
      new MockCustomerRepository(store),
    ),
    visit: assertRepositoryContract("visit", new MockVisitRepository(store)),
    opportunity: assertRepositoryContract(
      "opportunity",
      new MockOpportunityRepository(store),
    ),
    sale: assertRepositoryContract("sale", new MockSalesRepository(store)),
    erpSync: assertRepositoryContract(
      "erpSync",
      new MockErpSyncRepository(store),
    ),
    audit: assertRepositoryContract("audit", new MockAuditRepository(store)),
  };
  const customerService = new CustomerService({ repositories, store });
  const visitService = new VisitService({ repositories, store });
  const salesService = new SalesService({ repositories, store });
  const opportunityService = new OpportunityService({ repositories, store });
  const erpService = new ErpService({
    repositories,
    store,
    erpAdapter: options.erpAdapter || new MockErpAdapter(),
    salesService,
  });
  return {
    customerService,
    visitService,
    opportunityService,
    salesService,
    erpService,
    repositories,
    store,
    reset: () => store.reset(),
  };
}
