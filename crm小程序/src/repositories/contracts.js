export const RepositoryContract = Object.freeze({
  customer: ["list", "getById", "create", "update", "delete", "findDuplicate"],
  visit: ["list", "getById", "create", "update"],
  opportunity: ["list", "getById", "create", "update"],
  sale: ["list", "getById", "create", "update"],
  erpSync: ["list", "getById", "findBySaleId", "create", "update"],
  audit: ["list", "create"],
});

export function assertRepositoryContract(type, repository) {
  for (const method of RepositoryContract[type] || []) {
    if (typeof repository?.[method] !== "function")
      throw new Error(`${type}Repository 缺少 ${method} 方法`);
  }
  return repository;
}
