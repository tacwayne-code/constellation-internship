export const money = (value) =>
  new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(Number(value || 0));

export const shortMoney = (value) => {
  const number = Number(value || 0);
  return number >= 10000
    ? `¥${(number / 10000).toFixed(number % 10000 ? 1 : 0)}万`
    : `¥${number}`;
};

export const todayText = (date = new Date()) => {
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return `${date.getMonth() + 1}月${date.getDate()}日 ${weekdays[date.getDay()]}`;
};
