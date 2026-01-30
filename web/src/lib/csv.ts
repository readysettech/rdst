function escapeCsvCell(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }

  const cell = String(value);
  const escaped = cell.replace(/"/g, "\"\"");
  const requiresQuotes = /[",\r\n]/.test(cell);

  if (requiresQuotes) {
    return `"${escaped}"`;
  }

  return escaped;
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  const headerRow = columns.map((column) => escapeCsvCell(column)).join(",");
  const dataRows = rows.map((row) => row.map((cell) => escapeCsvCell(cell)).join(","));

  return [headerRow, ...dataRows].join("\r\n");
}

export function createCsvFilename(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const timestamp = [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("") + `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;

  return `rdst-query-results-${timestamp}.csv`;
}

export function downloadCsv(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
