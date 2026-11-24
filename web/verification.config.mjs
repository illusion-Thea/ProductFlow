import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import process from "node:process";

// Kept in a Node module so the browser's type environment has no Node globals.
// No dotenv loading is allowed when an isolated manifest is present.
export function verificationEnvironment() {
  if (!process.env.M101_MANIFEST) return null;
  const env = process.env;
  const manifest = JSON.parse(readFileSync(env.M101_MANIFEST, "utf8"));
  if (!manifest.stub_only || !manifest.isolation_preflight_passed
    || env.VITE_VERIFICATION_ENVIRONMENT_ID !== manifest.environment_id
    || env.VITE_DEV_PROXY_TARGET !== `http://127.0.0.1:${manifest.ports.api}`
    || Number(env.WEB_PORT) !== manifest.ports.web || env.VITE_API_BASE_URL) {
    throw new Error("Verification proxy/environment mismatch");
  }
  const root = realpathSync(manifest.run_root);
  for (const path of [env.M101_WEB_CACHE, env.M101_WEB_DIST]) {
    if (!path) throw new Error("Verification output directory required");
    let ancestor = resolve(path);
    while (!existsSync(ancestor)) ancestor = dirname(ancestor);
    const actual = resolve(realpathSync(ancestor), relative(ancestor, resolve(path)));
    const child = relative(root, actual);
    if (!child || child.startsWith("..") || isAbsolute(child)) throw new Error("Unsafe verification output path");
  }
  return env;
}
