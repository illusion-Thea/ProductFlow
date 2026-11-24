import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { executionIntent } from "./executionIntent";

const environment = "m101-test-browser";
const draft = { sku: "ONE", source_asset_ids: ["asset"], content_language: "zh-CN" as const,
  output_spec: { width: 32, height: 32, test_only: true as const } };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("VITE_VERIFICATION_ENVIRONMENT_ID", environment);
  const records = new Map<string, string>();
  vi.stubGlobal("sessionStorage", { getItem: (key: string) => records.get(key) ?? null,
    setItem: (key: string, value: string) => { records.set(key, value); }, removeItem: (key: string) => { records.delete(key); } });
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("controlled API submission", () => {
  it("retains JSON Content-Type, credentials and the explicit idempotency header together", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ environment_id: environment, stub_only: true }))
      .mockResolvedValueOnce(response({ id: "accepted" }, 201));
    vi.stubGlobal("fetch", fetcher);
    const { api } = await import("./api");
    await api.createPrototype("product", "experiment", draft);
    const init = fetcher.mock.calls[1][1] as RequestInit;
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(init.headers).get("Idempotency-Key")).toMatch(/^[a-f0-9-]{36}$/);
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ ...draft, experiment_run_id: "experiment" });
  });
  it("reconciles an uncertain committed request without a second write", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ environment_id: environment, stub_only: true }))
      .mockRejectedValueOnce(new TypeError("fixture response lost"))
      .mockResolvedValueOnce(response({ status: "committed", response: { id: "original" } }));
    vi.stubGlobal("fetch", fetcher);
    const { api } = await import("./api");
    expect(await api.createPrototype("product", "experiment", draft)).toEqual({ id: "original" });
    expect(fetcher.mock.calls.filter(([, init]) => init.method === "POST")).toHaveLength(1);
  });
  it("reload reconciliation precedes changed version/payload comparison", async () => {
    const key = `productflow.execution:${environment}:product:product:create-prototype`;
    await executionIntent(sessionStorage, key, { ...draft, experiment_run_id: "experiment" });
    const fetcher = vi.fn().mockResolvedValueOnce(response({ environment_id: environment, stub_only: true }))
      .mockResolvedValueOnce(response({ status: "committed", response: { id: "original" } }));
    vi.stubGlobal("fetch", fetcher);
    const { api } = await import("./api");
    expect(await api.createPrototype("product", "experiment", { ...draft, sku: "changed locally" })).toEqual({ id: "original" });
    expect(fetcher.mock.calls.every(([, init]) => init.method !== "POST")).toBe(true);
    expect(sessionStorage.getItem(key)).toBeNull();
  });
  it("lookup miss never releases an unresolved different intent", async () => {
    const key = `productflow.execution:${environment}:product:product:create-prototype`;
    const intent = await executionIntent(sessionStorage, key, { ...draft, experiment_run_id: "experiment" });
    const fetcher = vi.fn().mockResolvedValueOnce(response({ environment_id: environment, stub_only: true }))
      .mockResolvedValueOnce(response({ status: "not_found" }));
    vi.stubGlobal("fetch", fetcher);
    const { api } = await import("./api");
    await expect(api.createPrototype("product", "experiment", { ...draft, sku: "new" })).rejects.toThrow("submission_unresolved");
    expect(JSON.parse(sessionStorage.getItem(key)!).key).toBe(intent.key);
    expect(fetcher.mock.calls).toHaveLength(2);
  });
});
