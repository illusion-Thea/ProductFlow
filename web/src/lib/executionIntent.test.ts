import { describe, expect, it } from "vitest";
import { clearExecutionIntent, executionIntent, type IntentStorage } from "./executionIntent";

function storage(): IntentStorage {
  const records = new Map<string, string>();
  return { getItem: (key) => records.get(key) ?? null, setItem: (key, value) => { records.set(key, value); }, removeItem: (key) => { records.delete(key); } };
}

describe("uncertain execution intent", () => {
  it("keeps one key across reloads and object field ordering", async () => {
    const saved = storage();
    const first = await executionIntent(saved, "run:task:generate", { a: 1, b: "中文" });
    expect(await executionIntent(saved, "run:task:generate", { b: "中文", a: 1 })).toEqual(first);
    expect(JSON.parse(saved.getItem("run:task:generate")!).payload).toEqual({ a: 1, b: "中文" });
    await expect(executionIntent(saved, "run:task:generate", { a: 2 })).rejects.toThrow("submission_unresolved");
    clearExecutionIntent(saved, "run:task:generate", "other-key");
    expect(saved.getItem("run:task:generate")).not.toBeNull();
    clearExecutionIntent(saved, "run:task:generate", first.key);
    expect((await executionIntent(saved, "run:task:generate", { a: 2 })).key).not.toBe(first.key);
  });
});
