import { ApiErpAdapter } from "../adapters/ApiErpAdapter.js";
import { assertRepositoryContract } from "../repositories/contracts.js";
import { ApiClient, ApiStore } from "../repositories/api/ApiClient.js";
import {
  ApiAuditRepository,
  ApiCustomerRepository,
  ApiErpSyncRepository,
  ApiOpportunityRepository,
  ApiSalesRepository,
  ApiVisitRepository,
} from "../repositories/api/ApiRepositories.js";
import { CustomerService } from "../services/CustomerService.js";
import { ErpService } from "../services/ErpService.js";
import { OpportunityService } from "../services/OpportunityService.js";
import { SalesService } from "../services/SalesService.js";
import { VisitService } from "../services/VisitService.js";

export function createApiServiceRegistry(options = {}) {
  const client = new ApiClient({
    baseUrl: options.baseUrl || "/api",
    fetchImpl: options.fetchImpl,
    actor: options.actor,
  });
  const store = new ApiStore(client);
  const repositories = {
    customer: assertRepositoryContract(
      "customer",
      new ApiCustomerRepository(client),
    ),
    visit: assertRepositoryContract("visit", new ApiVisitRepository(client)),
    opportunity: assertRepositoryContract(
      "opportunity",
      new ApiOpportunityRepository(client),
    ),
    sale: assertRepositoryContract("sale", new ApiSalesRepository(client)),
    erpSync: assertRepositoryContract(
      "erpSync",
      new ApiErpSyncRepository(client),
    ),
    audit: assertRepositoryContract("audit", new ApiAuditRepository(client)),
  };
  const customerService = new CustomerService({ repositories, store });
  const visitService = new VisitService({ repositories, store });
  const salesService = new SalesService({ repositories, store });
  const opportunityService = new OpportunityService({ repositories, store });
  const erpService = new ErpService({
    repositories,
    store,
    erpAdapter: options.erpAdapter || new ApiErpAdapter(client),
    salesService,
  });

  return {
    mode: "SHARED_API",
    customerService,
    visitService,
    opportunityService,
    salesService,
    erpService,
    repositories,
    store,
    setActor: (actor) => client.setActor(actor),
    reset: () => store.reset(),
  };
}
