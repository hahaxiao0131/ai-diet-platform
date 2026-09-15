export const ACCOUNT_DELETE_CONFIRMATION = "永久删除";

export function isAccountDeleteConfirmation(value: string): boolean {
  return value.trim() === ACCOUNT_DELETE_CONFIRMATION;
}

export function accountExportFilename(exportedAt: string): string {
  const day = exportedAt.slice(0, 10).replace(/[^0-9-]/g, "") || "data";
  return `diet-account-export-${day}.json`;
}

export function serializeAccountExport(data: unknown): string {
  return JSON.stringify(data, null, 2);
}
