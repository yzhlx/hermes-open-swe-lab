import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_LINES,
  truncateHead,
  withFileMutationQueue,
} from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  writeFile,
} from "node:fs/promises";
import path from "node:path";

import {
  assertSafeContent,
  isProtectedRelativePath,
  resolveReadable,
  resolveWritable,
} from "./pi_workspace_guard.mjs";

const REQUIRED_ROOT = process.env.HERMES_PI_WORKSPACE_ROOT ?? "";
const TOOL_NAMES = ["read", "write", "edit", "ls", "find", "grep", "submit_result"];

function stableFailure(error: unknown): Error {
  const message = error instanceof Error ? error.message : "tool_failed";
  const allowed = new Set([
    "workspace_path_denied",
    "workspace_symlink_denied",
    "workspace_root_invalid",
    "protected_path",
    "credential_like_content",
    "invalid_content",
    "file_not_found",
    "not_a_file",
    "not_a_directory",
    "invalid_regex",
    "edit_match_missing",
    "edit_match_not_unique",
    "edit_overlap",
  ]);
  return new Error(allowed.has(message) ? message : "tool_failed");
}

function relativeDisplay(root: string, absolute: string): string {
  return path.relative(root, absolute).replaceAll("\\", "/") || ".";
}

function truncated(text: string): string {
  return truncateHead(text, {
    maxBytes: DEFAULT_MAX_BYTES,
    maxLines: DEFAULT_MAX_LINES,
  }).content;
}

function globRegex(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replaceAll("**", "\u0000")
    .replaceAll("*", "[^/]*")
    .replaceAll("?", "[^/]")
    .replaceAll("\u0000", ".*");
  return new RegExp(`^${escaped}$`, "i");
}

async function containedFiles(root: string, start: string, limit: number): Promise<string[]> {
  const output: string[] = [];
  async function visit(directory: string): Promise<void> {
    if (output.length >= limit) return;
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (output.length >= limit || entry.isSymbolicLink()) continue;
      const absolute = path.join(directory, entry.name);
      const relative = relativeDisplay(root, absolute);
      if (isProtectedRelativePath(relative)) continue;
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile()) output.push(absolute);
    }
  }
  await visit(start);
  return output;
}

export default async function (pi: ExtensionAPI) {
  if (!REQUIRED_ROOT) throw new Error("pi_workspace_root_required");
  const root = await realpath(REQUIRED_ROOT);
  const cwd = await realpath(process.cwd());
  const comparable = (value: string) => process.platform === "win32" ? value.toLowerCase() : value;
  if (comparable(root) !== comparable(cwd)) throw new Error("pi_workspace_binding_failure");

  pi.registerTool({
    name: "read",
    label: "Read workspace file",
    description: "Read one regular text file inside the exact task worktree. External, symlink, .git, .env, key, credential, and diagnostic paths are denied. Output is capped at 50KB/2000 lines.",
    parameters: Type.Object({
      path: Type.String(),
      offset: Type.Optional(Type.Integer({ minimum: 1 })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 2000 })),
    }),
    async execute(_id, params) {
      try {
        const absolute = await resolveReadable(root, params.path);
        const stat = await lstat(absolute);
        if (!stat.isFile()) throw new Error("not_a_file");
        const content = await readFile(absolute, "utf8");
        const lines = content.split("\n");
        const start = Math.max(0, (params.offset ?? 1) - 1);
        const selected = lines.slice(start, params.limit ? start + params.limit : undefined).join("\n");
        return { content: [{ type: "text", text: truncated(selected) }], details: { path: relativeDisplay(root, absolute) } };
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "write",
    label: "Write workspace file",
    description: "Create or overwrite one regular file inside the exact task worktree. Credential-like content and protected paths are denied.",
    parameters: Type.Object({ path: Type.String(), content: Type.String({ maxLength: 500000 }) }),
    async execute(_id, params) {
      try {
        assertSafeContent(params.content);
        const absolute = await resolveWritable(root, params.path);
        return await withFileMutationQueue(absolute, async () => {
          await mkdir(path.dirname(absolute), { recursive: true });
          await writeFile(absolute, params.content, "utf8");
          return { content: [{ type: "text", text: `Wrote ${relativeDisplay(root, absolute)}` }], details: { path: relativeDisplay(root, absolute) } };
        });
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "edit",
    label: "Edit workspace file",
    description: "Apply unique non-overlapping exact text replacements to one regular file inside the exact task worktree.",
    parameters: Type.Object({
      path: Type.String(),
      edits: Type.Array(Type.Object({ oldText: Type.String({ minLength: 1 }), newText: Type.String() }), { minItems: 1, maxItems: 50 }),
    }),
    async execute(_id, params) {
      try {
        for (const edit of params.edits) assertSafeContent(edit.newText);
        const absolute = await resolveReadable(root, params.path);
        return await withFileMutationQueue(absolute, async () => {
          const stat = await lstat(absolute);
          if (!stat.isFile()) throw new Error("not_a_file");
          const current = await readFile(absolute, "utf8");
          const regions = params.edits.map((edit) => {
            const start = current.indexOf(edit.oldText);
            if (start < 0) throw new Error("edit_match_missing");
            if (current.indexOf(edit.oldText, start + 1) >= 0) throw new Error("edit_match_not_unique");
            return { start, end: start + edit.oldText.length, newText: edit.newText };
          }).sort((a, b) => a.start - b.start);
          for (let i = 1; i < regions.length; i += 1) {
            if (regions[i].start < regions[i - 1].end) throw new Error("edit_overlap");
          }
          let next = current;
          for (const region of regions.toReversed()) {
            next = next.slice(0, region.start) + region.newText + next.slice(region.end);
          }
          await writeFile(absolute, next, "utf8");
          return { content: [{ type: "text", text: `Edited ${relativeDisplay(root, absolute)}` }], details: { path: relativeDisplay(root, absolute), edits: regions.length } };
        });
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "ls",
    label: "List workspace directory",
    description: "List non-symlink, non-protected entries in one workspace directory.",
    parameters: Type.Object({ path: Type.Optional(Type.String()), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 500 })) }),
    async execute(_id, params) {
      try {
        const absolute = await resolveReadable(root, params.path ?? ".");
        const stat = await lstat(absolute);
        if (!stat.isDirectory()) throw new Error("not_a_directory");
        const entries = (await readdir(absolute, { withFileTypes: true }))
          .filter((entry) => !entry.isSymbolicLink())
          .map((entry) => ({ entry, relative: relativeDisplay(root, path.join(absolute, entry.name)) }))
          .filter(({ relative }) => !isProtectedRelativePath(relative))
          .sort((a, b) => a.relative.localeCompare(b.relative))
          .slice(0, params.limit ?? 500)
          .map(({ entry, relative }) => `${relative}${entry.isDirectory() ? "/" : ""}`);
        return { content: [{ type: "text", text: truncated(entries.join("\n")) }], details: { count: entries.length } };
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "find",
    label: "Find workspace files",
    description: "Find regular non-symlink, non-protected workspace files by glob. Results are capped at 200.",
    parameters: Type.Object({ pattern: Type.String({ minLength: 1 }), path: Type.Optional(Type.String()), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 200 })) }),
    async execute(_id, params) {
      try {
        const start = await resolveReadable(root, params.path ?? ".");
        const stat = await lstat(start);
        if (!stat.isDirectory()) throw new Error("not_a_directory");
        const matcher = globRegex(params.pattern.replaceAll("\\", "/"));
        const files = await containedFiles(root, start, params.limit ?? 200);
        const matches = files.map((file) => relativeDisplay(root, file)).filter((file) => matcher.test(file));
        return { content: [{ type: "text", text: truncated(matches.join("\n")) }], details: { count: matches.length } };
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "grep",
    label: "Search workspace text",
    description: "Search regular non-symlink, non-protected workspace text files with a JavaScript regular expression. Results are capped at 200.",
    parameters: Type.Object({ pattern: Type.String({ minLength: 1 }), path: Type.Optional(Type.String()), glob: Type.Optional(Type.String()), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 200 })) }),
    async execute(_id, params) {
      try {
        let matcher;
        try { matcher = new RegExp(params.pattern, "i"); } catch { throw new Error("invalid_regex"); }
        const start = await resolveReadable(root, params.path ?? ".");
        const stat = await lstat(start);
        if (!stat.isDirectory()) throw new Error("not_a_directory");
        const fileMatcher = params.glob ? globRegex(params.glob.replaceAll("\\", "/")) : null;
        const limit = params.limit ?? 200;
        const files = await containedFiles(root, start, 2000);
        const output: string[] = [];
        for (const file of files) {
          const relative = relativeDisplay(root, file);
          if (fileMatcher && !fileMatcher.test(relative)) continue;
          const stat = await lstat(file);
          if (stat.size > 1024 * 1024) continue;
          const content = await readFile(file, "utf8");
          if (content.includes("\0")) continue;
          for (const [index, line] of content.split("\n").entries()) {
            if (matcher.test(line)) output.push(`${relative}:${index + 1}:${line.slice(0, 1000)}`);
            matcher.lastIndex = 0;
            if (output.length >= limit) break;
          }
          if (output.length >= limit) break;
        }
        return { content: [{ type: "text", text: truncated(output.join("\n")) }], details: { count: output.length } };
      } catch (error) {
        throw stableFailure(error);
      }
    },
  });

  pi.registerTool({
    name: "submit_result",
    label: "Submit coding result",
    description: "Use as the final action after workspace edits are complete or blocked.",
    parameters: Type.Object({
      status: StringEnum(["completed", "blocked"] as const),
      summary: Type.String({ minLength: 1, maxLength: 4000 }),
    }),
    async execute(_id, params) {
      return {
        content: [{ type: "text", text: "Host Pi Agent result submitted." }],
        details: { status: params.status, summary: params.summary },
        terminate: true,
      };
    },
  });

  pi.on("session_start", () => {
    pi.setActiveTools(TOOL_NAMES);
  });
}
