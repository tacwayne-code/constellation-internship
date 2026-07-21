export const TEST_CODE = "123456";

export const users = {
  13800138000: {
    id: "USR-00018",
    name: "王晨",
    phone: "13800138000",
    role: "销售人员",
    dataScope: "ALL",
  },
  13900139000: {
    id: "USR-00001",
    name: "李娜",
    phone: "13900139000",
    role: "销售经理",
    dataScope: "ALL",
  },
};

export const seedData = {
  customers: [
    {
      id: "CUS-1001",
      name: "上海宏图机械有限公司",
      contact: "张伟",
      phone: "13800138000",
      address: "上海市松江区九亭镇久富开发区",
      owner: "王晨",
      status: "意向沟通",
      nextFollow: "2026-07-20",
      note: "关注新产线自动化升级。",
    },
    {
      id: "CUS-1002",
      name: "苏州智创电子科技",
      contact: "李娜",
      phone: "15688881234",
      address: "苏州市工业园区星湖街",
      owner: "王晨",
      status: "跟进中",
      nextFollow: "2026-07-18",
      note: "需要补充规格清单。",
    },
    {
      id: "CUS-1003",
      name: "杭州星辉贸易有限公司",
      contact: "陈刚",
      phone: "18767123344",
      address: "杭州市滨江区江南大道",
      owner: "王晨",
      status: "初步接触",
      nextFollow: "2026-07-22",
      note: "",
    },
    {
      id: "CUS-1004",
      name: "宁波远航工业设备有限公司",
      contact: "周敏",
      phone: "18657401288",
      address: "宁波市鄞州区金谷北路",
      owner: "李娜",
      status: "跟进中",
      nextFollow: "2026-07-25",
      note: "用于验证经理授权范围。",
    },
  ],
  visits: [
    {
      id: "VIS-2001",
      customerId: "CUS-1001",
      customerName: "上海宏图机械有限公司",
      arrivedAt: "2026-07-15T09:30",
      location: "上海市松江区九亭镇久富开发区",
      content: "确认新产线升级需求，客户希望下周提供技术方案。",
      nextFollow: "2026-07-20",
      photo: "",
      note: "",
      createdBy: "USR-00018",
    },
    {
      id: "VIS-2002",
      customerId: "CUS-1002",
      customerName: "苏州智创电子科技",
      arrivedAt: "2026-07-14T14:00",
      location: "苏州市工业园区星湖街",
      content: "电话跟进，等待客户发送现有设备规格。",
      nextFollow: "2026-07-18",
      photo: "",
      note: "",
      createdBy: "USR-00018",
    },
  ],
  intentions: [
    {
      id: "INT-3001",
      customerId: "CUS-1001",
      customerName: "上海宏图机械有限公司",
      product: "自动化装配线",
      spec: "AL-300",
      qty: 2,
      price: 280000,
      closeDate: "2026-08-15",
      stage: "方案沟通",
      note: "预计采购两条线。",
      createdBy: "USR-00018",
    },
    {
      id: "INT-3002",
      customerId: "CUS-1002",
      customerName: "苏州智创电子科技",
      product: "视觉检测模组",
      spec: "VI-08",
      qty: 6,
      price: 48000,
      closeDate: "2026-08-30",
      stage: "需求确认",
      note: "",
      createdBy: "USR-00018",
    },
  ],
  sales: [
    {
      id: "SAL-202607-0001",
      customerId: "CUS-1003",
      customerName: "杭州星辉贸易有限公司",
      product: "伺服控制柜",
      spec: "SC-1200",
      qty: 3,
      price: 86000,
      deliveryDate: "2026-08-10",
      status: "草稿",
      attachment: "",
      note: "",
      erpStatus: "待后续接入",
      createdBy: "USR-00018",
    },
  ],
};

export const cloneSeed = () => JSON.parse(JSON.stringify(seedData));

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

export const todayText = () => "7月15日 周三";

export const makeId = (prefix) =>
  `${prefix}-${Date.now().toString().slice(-8)}`;
