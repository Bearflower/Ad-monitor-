import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const sourcePath =
  "/Users/yl/Documents/跨境电商/广告盯盘自动化/.worktrees/" +
  "semi-automatic-sku-cost/var/imports/shopee/merged-order-cost-data.json";
const outputPath =
  "/Users/yl/Documents/跨境电商/广告盯盘自动化/.worktrees/" +
  "semi-automatic-sku-cost/outputs/shopee-cost-20260728/待补SKU成本-shopee.xlsx";
const previewPath =
  "/Users/yl/Documents/跨境电商/广告盯盘自动化/.worktrees/" +
  "semi-automatic-sku-cost/outputs/shopee-cost-20260728/preview.png";

const data = JSON.parse(await fs.readFile(sourcePath, "utf8"));
const workbook = Workbook.create();
const guide = workbook.worksheets.add("填写说明");
const costs = workbook.worksheets.add("待补SKU成本");
const orders = workbook.worksheets.add("订单明细（系统）");
const sources = workbook.worksheets.add("导入记录");

const navy = "#17365D";
const blue = "#1F4E78";
const paleBlue = "#D9EAF7";
const paleYellow = "#FFF2CC";
const paleGreen = "#E2F0D9";
const paleRed = "#FCE4D6";
const gray = "#667085";
const lightBorder = "#D0D5DD";

guide.showGridLines = false;
guide.getRange("A1:H1").merge();
guide.getRange("A1").values = [["Shopee SKU 成本补充表"]];
guide.getRange("A1:H1").format = {
  fill: navy,
  font: { bold: true, color: "#FFFFFF", size: 18 },
  verticalAlignment: "center",
};
guide.getRange("A1:H1").format.rowHeight = 34;
guide.getRange("A3:B8").values = [
  ["你只需要做什么", "打开“待补SKU成本”，填写黄色的“单件成本（人民币）”列。"],
  ["成本口径", "填写该 Seller SKU 对应一件销售单位的完整采购成本（人民币）。"],
  ["无需填写", "订单号、状态、物流、SKU、规格、数量均已由 Shopee 报表自动填充。"],
  ["退货处理", "退货件数由系统保留；成本主数据仍按 SKU 维护，不需要重复填写。"],
  ["取消订单", "取消订单保留在订单明细中，但不会生成待补成本任务。"],
  ["完成标准", "“待填写”数量变为 0 后，即可导入系统进行利润和 ROAS 分析。"],
];
guide.getRange("A3:A8").format = {
  fill: paleBlue,
  font: { bold: true, color: navy },
  verticalAlignment: "top",
};
guide.getRange("B3:B8").format = {
  wrapText: true,
  verticalAlignment: "top",
};
guide.getRange("A3:B8").format.borders = {
  preset: "all",
  style: "thin",
  color: lightBorder,
};
guide.getRange("A3:A8").format.columnWidth = 18;
guide.getRange("B3:B8").format.columnWidth = 72;
guide.getRange("A10:B15").values = [
  ["数据范围", `${data.file_summary[0].min_date} 至 ${data.file_summary.at(-1).max_date}`],
  ["Shopee 订单数", data.unique_order_count],
  ["有效订单数（不含取消）", data.noncancel_order_count],
  ["Seller SKU 数", data.sku_count],
  ["已填写成本", data.sku_count - data.pending_cost_count],
  ["待填写成本", data.pending_cost_count],
];
guide.getRange("A10:B15").format.borders = {
  preset: "all",
  style: "thin",
  color: lightBorder,
};
guide.getRange("A10:A15").format = {
  fill: "#F2F4F7",
  font: { bold: true, color: gray },
};
guide.getRange("B10:B15").format.numberFormat = [["@"], ["0"], ["0"], ["0"], ["0"], ["0"]];

costs.showGridLines = false;
costs.freezePanes.freezeRows(5);
costs.freezePanes.freezeColumns(3);
costs.getRange("A1:O1").merge();
costs.getRange("A1").values = [["待补 SKU 成本（只填写黄色列）"]];
costs.getRange("A1:O1").format = {
  fill: navy,
  font: { bold: true, color: "#FFFFFF", size: 16 },
  verticalAlignment: "center",
};
costs.getRange("A1:O1").format.rowHeight = 32;
costs.getRange("A2:B3").values = [
  ["待填写 SKU", data.pending_cost_count],
  ["SKU 总数", data.sku_count],
];
costs.getRange("D2:E3").values = [
  ["订单覆盖", data.unique_order_count],
  ["有效订单", data.noncancel_order_count],
];
for (const range of ["A2:B3", "D2:E3"]) {
  costs.getRange(range).format.borders = {
    preset: "all",
    style: "thin",
    color: lightBorder,
  };
}
costs.getRange("A2:A3").format =
  costs.getRange("D2:D3").format = {
    fill: paleBlue,
    font: { bold: true, color: navy },
  };
costs.getRange("G2:O3").merge();
costs.getRange("G2").values = [[
  "黄色列为唯一必填项；成本状态会自动更新。请不要修改灰色系统字段。",
]];
costs.getRange("G2:O3").format = {
  fill: paleYellow,
  font: { color: "#7F6000" },
  wrapText: true,
  verticalAlignment: "center",
};

const costHeaders = [
  "平台",
  "店铺",
  "Seller SKU",
  "规格",
  "商品名称",
  "首次订单日期",
  "最近订单日期",
  "相关订单数",
  "销售件数",
  "退货件数",
  "净件数",
  "成本状态",
  "单件成本（人民币）",
  "成本生效日期",
  "成本备注",
];
costs.getRange("A5:O5").values = [costHeaders];
costs.getRange("A5:O5").format = {
  fill: blue,
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
costs.getRange("A5:O5").format.rowHeight = 34;

const costRows = data.sku_rows.map((row) => [
  row.platform,
  row.store,
  row.seller_sku,
  row.variation,
  row.product_name,
  new Date(`${row.first_date}T00:00:00`),
  new Date(`${row.last_date}T00:00:00`),
  row.order_count,
  row.quantity,
  row.returned_quantity,
  row.net_quantity,
  null,
  row.unit_cost_cny,
  row.cost_effective_date
    ? new Date(`${row.cost_effective_date}T00:00:00`)
    : null,
  row.cost_note,
]);
const costEnd = 5 + costRows.length;
costs.getRange(`B6:C${costEnd}`).format.numberFormat = "@";
costs.getRange(`A6:O${costEnd}`).values = costRows;
costs.getRange("L6").formulas = [['=IF(M6="","待填写","已填写")']];
costs.getRange(`L6:L${costEnd}`).fillDown();
costs.getRange(`F6:G${costEnd}`).format.numberFormat = "yyyy-mm-dd";
costs.getRange(`N6:N${costEnd}`).format.numberFormat = "yyyy-mm-dd";
costs.getRange(`H6:K${costEnd}`).format.numberFormat = "#,##0";
costs.getRange(`M6:M${costEnd}`).format.numberFormat = '¥#,##0.00';
costs.getRange(`M6:M${costEnd}`).format = {
  fill: paleYellow,
  font: { bold: true, color: "#7F6000" },
};
costs.getRange(`A6:O${costEnd}`).format.borders = {
  insideHorizontal: { style: "thin", color: "#EAECF0" },
};
costs.getRange(`A6:O${costEnd}`).format.verticalAlignment = "top";
costs.getRange(`D6:E${costEnd}`).format.wrapText = true;
costs.getRange(`L6:L${costEnd}`).conditionalFormats.add("containsText", {
  text: "待填写",
  format: { fill: paleRed, font: { color: "#C00000", bold: true } },
});
costs.getRange(`L6:L${costEnd}`).conditionalFormats.add("containsText", {
  text: "已填写",
  format: { fill: paleGreen, font: { color: "#375623", bold: true } },
});
costs.tables.add(`A5:O${costEnd}`, true, "PendingSkuCosts");
const costWidths = [10, 16, 28, 22, 48, 13, 13, 11, 10, 10, 10, 11, 18, 15, 24];
costWidths.forEach((width, i) => {
  costs.getRangeByIndexes(0, i, costEnd, 1).format.columnWidth = width;
});

orders.showGridLines = false;
orders.freezePanes.freezeRows(1);
orders.freezePanes.freezeColumns(2);
const orderHeaders = [
  "订单日期",
  "订单号",
  "订单状态",
  "Shopee 原始状态",
  "物流状态",
  "配送方式",
  "物流单号",
  "Seller SKU",
  "商品名称",
  "规格",
  "数量",
  "退货数量",
  "来源文件",
];
orders.getRange("A1:M1").values = [orderHeaders];
orders.getRange("A1:M1").format = {
  fill: blue,
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
orders.getRange("A1:M1").format.rowHeight = 32;
const orderRows = data.order_lines.map((row) => [
  new Date(`${row.order_date}T00:00:00`),
  row.order_id,
  row.order_status,
  row.raw_order_status,
  row.shipping_status,
  row.shipping_method,
  row.tracking_number,
  row.seller_sku,
  row.product_name,
  row.variation,
  row.quantity,
  row.returned_quantity,
  row.source_file,
]);
const orderEnd = 1 + orderRows.length;
orders.getRange(`B2:B${orderEnd}`).format.numberFormat = "@";
orders.getRange(`G2:H${orderEnd}`).format.numberFormat = "@";
orders.getRange(`A2:M${orderEnd}`).values = orderRows;
orders.getRange(`A2:A${orderEnd}`).format.numberFormat = "yyyy-mm-dd";
orders.getRange(`K2:L${orderEnd}`).format.numberFormat = "#,##0";
orders.getRange(`C2:M${orderEnd}`).format.wrapText = true;
orders.getRange(`A2:M${orderEnd}`).format.verticalAlignment = "top";
orders.getRange(`A2:M${orderEnd}`).format.borders = {
  insideHorizontal: { style: "thin", color: "#EAECF0" },
};
orders.tables.add(`A1:M${orderEnd}`, true, "ShopeeOrderLines");
const orderWidths = [13, 20, 20, 44, 18, 22, 24, 28, 48, 24, 9, 11, 36];
orderWidths.forEach((width, i) => {
  orders.getRangeByIndexes(0, i, orderEnd, 1).format.columnWidth = width;
});

sources.showGridLines = false;
sources.getRange("A1:E1").values = [["来源文件", "明细行数", "订单数", "最早订单日期", "最晚订单日期"]];
sources.getRange("A1:E1").format = {
  fill: blue,
  font: { bold: true, color: "#FFFFFF" },
};
const sourceRows = data.file_summary.map((row) => [
  row.file,
  row.rows,
  row.orders,
  new Date(`${row.min_date}T00:00:00`),
  new Date(`${row.max_date}T00:00:00`),
]);
sources.getRange(`A2:E${sourceRows.length + 1}`).values = sourceRows;
sources.getRange(`D2:E${sourceRows.length + 1}`).format.numberFormat = "yyyy-mm-dd";
sources.getRange(`B2:C${sourceRows.length + 1}`).format.numberFormat = "#,##0";
sources.getRange(`A1:E${sourceRows.length + 1}`).format.borders = {
  preset: "all",
  style: "thin",
  color: lightBorder,
};
sources.getRange("A:A").format.columnWidth = 38;
sources.getRange("B:E").format.columnWidth = 16;
sources.freezePanes.freezeRows(1);

await fs.mkdir(outputPath.slice(0, outputPath.lastIndexOf("/")), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const preview = await workbook.render({
  sheetName: "待补SKU成本",
  range: "A1:O18",
  scale: 1.25,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const inspect = await workbook.inspect({
  kind: "table",
  range: `待补SKU成本!A1:O12`,
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 15,
  maxChars: 6000,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(inspect.ndjson);
console.log(errors.ndjson);
console.log(JSON.stringify({ outputPath, previewPath, costEnd, orderEnd }));
