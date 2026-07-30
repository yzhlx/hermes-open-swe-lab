import { lstat, realpath } from "node:fs/promises";
import path from "node:path";

const PROTECTED_SEGMENTS = new Set([".git", ".pi", ".agents", ".hermes"]);
const PROTECTED_BASENAMES = new Set([
  "credentials.json",
  "credentials.yaml",
  "credentials.yml",
  "secrets.json",
  "secrets.yaml",
  "secrets.yml",
  "id_rsa",
  "id_ed25519",
]);
const PROTECTED_EXTENSIONS = new Set([".pem", ".key", ".p12", ".pfx"]);
const CREDENTIAL_PATTERNS = [
  /-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----/,
  /\bgh[pousr]_[A-Za-z0-9]{30,}\b/,
  /\bsk-[A-Za-z0-9_-]{20,}\b/,
  /\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b/,
];

function stableError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function normalizeInput(inputPath) {
  if (typeof inputPath !== "string" || inputPath.includes("\0")) {
    throw stableError("workspace_path_denied");
  }
  const value = inputPath.startsWith("@") ? inputPath.slice(1) : inputPath;
  if (!value.trim()) {
    throw stableError("workspace_path_denied");
  }
  return value;
}

function comparable(value) {
  const normalized = path.normalize(value);
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function assertWithin(root, candidate) {
  const relative = path.relative(comparable(root), comparable(candidate));
  if (relative === "") return;
  if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw stableError("workspace_path_denied");
  }
}

export function isProtectedRelativePath(relativePath) {
  const normalized = relativePath.replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  for (const part of parts) {
    const lowered = part.toLowerCase();
    if (PROTECTED_SEGMENTS.has(lowered)) return true;
    if (lowered === ".env" || lowered.startsWith(".env.")) return true;
  }
  const basename = (parts.at(-1) ?? "").toLowerCase();
  return PROTECTED_BASENAMES.has(basename) || PROTECTED_EXTENSIONS.has(path.extname(basename));
}

export function assertSafeRelativePath(root, candidate) {
  assertWithin(root, candidate);
  const relative = path.relative(root, candidate);
  if (isProtectedRelativePath(relative)) {
    throw stableError("protected_path");
  }
  return candidate;
}

async function canonicalRoot(root) {
  const resolved = path.resolve(root);
  const canonical = await realpath(resolved);
  const stat = await lstat(canonical);
  if (!stat.isDirectory()) throw stableError("workspace_root_invalid");
  return canonical;
}

async function rejectSymlinkComponents(root, candidate, allowMissing) {
  const relative = path.relative(root, candidate);
  const parts = relative === "" ? [] : relative.split(path.sep).filter(Boolean);
  let cursor = root;
  for (const part of parts) {
    cursor = path.join(cursor, part);
    try {
      const stat = await lstat(cursor);
      if (stat.isSymbolicLink()) throw stableError("workspace_symlink_denied");
    } catch (error) {
      if (error?.code === "ENOENT" && allowMissing) return;
      throw error;
    }
  }
}

async function resolveContained(root, inputPath, allowMissing) {
  const canonical = await canonicalRoot(root);
  const input = normalizeInput(inputPath);
  const candidate = path.resolve(canonical, input);
  assertSafeRelativePath(canonical, candidate);
  await rejectSymlinkComponents(canonical, candidate, allowMissing);
  if (!allowMissing) {
    const actual = await realpath(candidate);
    assertSafeRelativePath(canonical, actual);
  }
  return candidate;
}

export async function resolveReadable(root, inputPath) {
  return resolveContained(root, inputPath, false);
}

export async function resolveWritable(root, inputPath) {
  return resolveContained(root, inputPath, true);
}

export function assertSafeContent(content) {
  if (typeof content !== "string") throw stableError("invalid_content");
  if (CREDENTIAL_PATTERNS.some((pattern) => pattern.test(content))) {
    throw stableError("credential_like_content");
  }
  return content;
}
