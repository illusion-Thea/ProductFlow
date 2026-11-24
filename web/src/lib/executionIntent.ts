export interface ExecutionIntent { key: string; payloadHash: string }
export interface IntentStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }

export function readExecutionIntent(storage: IntentStorage, scope: string): ExecutionIntent | null {
  const saved = storage.getItem(scope);
  if (!saved) return null;
  const parsed: unknown = JSON.parse(saved);
  if (typeof parsed !== "object" || !parsed || !("key" in parsed) || typeof parsed.key !== "string"
    || !("payloadHash" in parsed) || typeof parsed.payloadHash !== "string") throw new Error("submission_unresolved");
  return { key: parsed.key, payloadHash: parsed.payloadHash };
}

function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, ordered(item)]));
  }
  return value;
}

export async function executionIntent(storage: IntentStorage, scope: string, payload: unknown): Promise<ExecutionIntent> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(ordered(payload))));
  const payloadHash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const saved = readExecutionIntent(storage, scope);
  if (saved) {
    if (saved.payloadHash !== payloadHash) throw new Error("submission_unresolved");
    return saved;
  }
  const intent = { key: crypto.randomUUID(), payloadHash };
  storage.setItem(scope, JSON.stringify(intent));
  return intent;
}

export function clearExecutionIntent(storage: IntentStorage, scope: string, key: string): void {
  const value: unknown = JSON.parse(storage.getItem(scope) ?? "null");
  if (value && typeof value === "object" && "key" in value && value.key === key) storage.removeItem(scope);
}
