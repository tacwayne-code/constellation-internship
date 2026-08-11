#!/usr/bin/env node
/**
 * 从 dist 编译产物提取 Mock 数据 → backend/mock_data/*.json
 *
 * 用法：node scripts/extract_mock_data.js
 * 依赖：无（纯 Node 标准库）
 */
const fs = require("fs");
const path = require("path");

const DIST_JS = path.resolve(__dirname, "../../dist/assets/index-CsqEi3vV.js");
const OUT_DIR = path.resolve(__dirname, "../mock_data");

/** 在文本中查找 startMark 之后第一个平衡括号（[ ] 或 { }）包裹的完整字面量文本 */
function extractBalanced(text, startIdx) {
  const opener = text[startIdx];
  const closer = opener === "[" ? "]" : "}";
  let depth = 0;
  let inString = null; // ` " '
  let i = startIdx;
  for (; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (ch === "\\") { i++; continue; }
      if (ch === inString) inString = null;
      continue;
    }
    if (ch === "`" || ch === '"' || ch === "'") { inString = ch; continue; }
    if (ch === opener) depth++;
    else if (ch === closer) {
      depth--;
      if (depth === 0) break;
    }
  }
  return text.slice(startIdx, i + 1);
}

/** 用 VM 解析 JS 字面量为 JS 值（允许模板字符串、方法等非 JSON 语法） */
function parseLiteral(literalText) {
  // 模板字符串中的 ${...} 在纯数据里不应出现；若有则替换为占位
  const clean = literalText.replace(/\$\{([^}]+)\}/g, (m) => `__TPL__`);
  const vm = require("vm");
  // 注入 bundle 中使用的行生成器函数 S()（来自 dist 反推）
  const sandbox = {
    S: (id, name, cells, status, tone, fields, progress = null) => ({
      id, name, cells, status, tone, fields, progress,
    }),
  };
  try {
    return vm.runInNewContext(`(${clean})`, sandbox, { timeout: 5000 });
  } catch (e) {
    console.error("  解析失败:", e.message.slice(0, 120));
    return null;
  }
}

function main() {
  if (!fs.existsSync(DIST_JS)) {
    console.error("未找到 dist bundle:", DIST_JS);
    process.exit(1);
  }
  const src = fs.readFileSync(DIST_JS, "utf8");
  fs.mkdirSync(OUT_DIR, { recursive: true });

  // 每个数据源：标记字符串 → 输出文件名
  const targets = [
    { mark: "id:`p1`", key: "projects", file: "projects.json", label: "项目组合" },
    { mark: "PKG-021", key: "delivery_packages", file: "delivery_packages.json", label: "交付包" },
    { mark: "id:`R-108`", key: "risks", file: "risks.json", label: "风险/问题" },
    { mark: "id:`EQ-284`", key: "procurement", file: "procurement.json", label: "采购项" },
    { mark: "LOG-032", key: "logistics", file: "logistics.json", label: "物流" },
    { mark: "MAT-A03-018", key: "inventory", file: "inventory.json", label: "现场物料" },
    { mark: "TEAM-E07", key: "people", file: "people.json", label: "外包班组" },
    { mark: "VEN-001", key: "vendors", file: "vendors.json", label: "供应商" },
  ];

  let summary = {};
  for (const t of targets) {
    const idx = src.indexOf(t.mark);
    if (idx < 0) {
      console.log(`[跳过] ${t.label}：未找到标记 ${t.mark}`);
      continue;
    }
    // 从标记往前找数组开始 "["
    const arrStart = src.lastIndexOf("[", idx);
    if (arrStart < 0) {
      console.log(`[跳过] ${t.label}：无法定位数组起点`);
      continue;
    }
    const literal = extractBalanced(src, arrStart);
    const value = parseLiteral(literal);
    if (value === null || !Array.isArray(value)) {
      console.log(`[失败] ${t.label}：解析结果非数组`);
      continue;
    }
    const outFile = path.join(OUT_DIR, t.file);
    fs.writeFileSync(outFile, JSON.stringify(value, null, 2), "utf8");
    summary[t.key] = { count: value.length, file: t.file };
    console.log(`[成功] ${t.label}：${value.length} 条 → mock_data/${t.file}`);
  }

  fs.writeFileSync(
    path.join(OUT_DIR, "_summary.json"),
    JSON.stringify({ extracted_at: new Date().toISOString(), ...summary }, null, 2),
    "utf8"
  );
  console.log("\n完成。摘要 → mock_data/_summary.json");
}

main();
