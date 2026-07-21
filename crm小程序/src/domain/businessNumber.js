const PREFIXES = Object.freeze({
  customer: "CUS",
  visit: "VIS",
  opportunity: "OPP",
  sale: "SALE",
  erpSync: "ERP",
  audit: "LOG",
});

function datePart(date) {
  const value = date instanceof Date ? date : new Date(date);
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}${month}${day}`;
}

export function formatBusinessNumber(type, date, sequence) {
  const prefix = PREFIXES[type];
  if (!prefix) throw new Error(`不支持的业务编号类型：${type}`);
  if (!Number.isInteger(sequence) || sequence < 1 || sequence > 9999)
    throw new Error("业务编号序号必须为1到9999的整数");
  return `${prefix}-${datePart(date)}-${String(sequence).padStart(4, "0")}`;
}

export function createBusinessNumberGenerator(initialCounters = {}) {
  const counters = { ...initialCounters };
  return {
    next(type, date = new Date()) {
      const key = `${type}:${datePart(date)}`;
      counters[key] = (counters[key] || 0) + 1;
      return formatBusinessNumber(type, date, counters[key]);
    },
    snapshot() {
      return { ...counters };
    },
  };
}
