export function resolveApiBaseUrl(configuredValue?: string | null): string {
  const configured = configuredValue?.trim();
  const base = configured || "/api/v1";
  const normalized = base.replace(/\/+$/, "");
  return normalized || "";
}
