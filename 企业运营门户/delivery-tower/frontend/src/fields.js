export const definitions = {
  projects: {
    title: "项目",
    fields: [
      ["name", "项目名称", "text", true],
      ["location", "现场位置", "text"],
      ["owner", "项目负责人", "text", true],
      ["due", "计划交付日期", "date"],
      ["status", "项目状态", ["进行中", "已完成", "暂停"]],
      ["description", "项目说明", "textarea"],
    ],
  },
  tasks: {
    title: "任务",
    fields: [
      ["title", "任务名称", "text", true],
      ["owner", "负责人", "text", true],
      ["assignee_id", "关联账号（用于我的待办）", "assignee"],
      ["due", "截止时间", "datetime-local"],
      ["priority", "优先级", ["中", "高", "低"]],
      ["status", "任务状态", ["待处理", "处理中", "已完成", "已取消"]],
      ["note", "任务说明", "textarea"],
    ],
  },
  issues: {
    title: "问题",
    fields: [
      ["title", "问题名称", "text", true],
      ["owner", "负责人", "text", true],
      ["priority", "风险等级", ["中", "高", "低"]],
      ["status", "处理状态", ["待处理", "处理中", "已解决"]],
      ["due", "计划解决日期", "date"],
      ["description", "问题说明", "textarea"],
      ["next", "下一步安排", "textarea"],
    ],
  },
  documents: {
    title: "资料",
    fields: [
      ["name", "资料名称", "text", true],
      ["type", "资料类型", ["图纸", "清单", "检查表", "其他"]],
      ["owner", "维护人", "text", true],
      ["source", "资料来源", "text"],
      ["state", "使用状态", ["待确认", "可使用", "已作废"]],
      ["note", "资料说明", "textarea"],
    ],
  },
  purchases: {
    title: "采购跟踪",
    fields: [
      ["name", "物资名称", "text", true],
      ["reference", "采购单号", "text"],
      ["spec", "规格", "text"],
      ["qty", "数量", "number", true],
      ["unit", "计量单位", "text", true],
      ["supplier", "供应商", "text"],
      ["date", "预计到货", "date"],
      [
        "status",
        "采购状态",
        ["待确认", "待发货", "运输中", "已到货", "到货延迟", "已取消"],
      ],
      ["owner", "负责人", "text", true],
      ["note", "备注", "textarea"],
    ],
  },
  inventory: {
    title: "物资",
    fields: [
      ["name", "物资名称", "text", true],
      ["spec", "规格", "text"],
      ["unit", "计量单位", "text", true],
      ["location", "存放位置", "text"],
      ["note", "备注", "textarea"],
    ],
  },
  people: {
    title: "现场人员",
    fields: [
      ["name", "姓名", "text", true],
      ["team", "班组", "text"],
      ["role", "现场岗位", "text"],
      ["task", "当前任务", "text"],
      ["state", "现场状态", ["在场", "已离场"]],
      ["note", "备注", "textarea"],
    ],
  },
};
export const nav = [
  ["overview", "项目概览"],
  ["tasks", "任务与问题"],
  ["documents", "图纸资料"],
  ["purchases", "采购跟踪"],
  ["inventory", "现场库存"],
  ["people", "外包人员"],
];
