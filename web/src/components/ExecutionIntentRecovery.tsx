import { useState } from "react";
import { PendingSubmissionError } from "../lib/api";

export function ExecutionIntentRecovery({ error, onRecovered }: {
  error: unknown; onRecovered: (result: unknown) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [completed, setCompleted] = useState<unknown>(null);
  if (!(error instanceof PendingSubmissionError) || completed === error) return null;
  const payload = error.originalPayload;
  const preview = payload && typeof payload === "object"
    ? ["sku" in payload ? payload.sku : null, "prompt" in payload ? payload.prompt : null].filter(Boolean).join(" · ") : "";
  return <div role="alert" className="my-2 rounded border border-amber-500 p-3 text-sm">
    <p>上次提交尚未确认。恢复会使用当时的内容和同一提交编号，当前修改仍保留。</p>
    {preview && <p className="max-h-24 overflow-auto whitespace-pre-wrap">{preview}</p>}
    <button type="button" disabled={busy} className="mt-2 rounded border px-3 py-1 disabled:opacity-40" onClick={async () => {
      setBusy(true); setMessage("");
      try {
        const result = await error.resume();
        setCompleted(error);
        await onRecovered(result);
      }
      catch (failure) { setMessage(failure instanceof Error ? failure.message : "恢复失败，请稍后再试。"); }
      finally { setBusy(false); }
    }}>{busy ? "正在恢复…" : "恢复上次提交"}</button>
    {message && <p>{message}</p>}
  </div>;
}
