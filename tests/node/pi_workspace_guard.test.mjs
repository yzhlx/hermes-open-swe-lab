import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertSafeContent,
  isProtectedRelativePath,
  resolveReadable,
  resolveWritable,
} from "../../deploy/worker/pi_workspace_guard.mjs";

async function fixture() {
  const parent = await mkdtemp(path.join(os.tmpdir(), "hermes-pi-guard-"));
  const root = path.join(parent, "repo");
  const outside = path.join(parent, "outside");
  await mkdir(path.join(root, "src"), { recursive: true });
  await mkdir(outside, { recursive: true });
  await writeFile(path.join(root, "src", "app.py"), "VALUE = 1\n", "utf8");
  await writeFile(path.join(outside, "secret.txt"), "outside\n", "utf8");
  return { parent, root, outside };
}

test("allows ordinary reads and nested writes", async () => {
  const { parent, root } = await fixture();
  try {
    assert.equal(await resolveReadable(root, "@src/app.py"), path.join(root, "src", "app.py"));
    assert.equal(await resolveWritable(root, "src/new/module.py"), path.join(root, "src", "new", "module.py"));
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("rejects traversal and absolute external paths", async () => {
  const { parent, root, outside } = await fixture();
  try {
    await assert.rejects(resolveReadable(root, "../outside/secret.txt"), /workspace_path_denied/);
    await assert.rejects(resolveReadable(root, path.join(outside, "secret.txt")), /workspace_path_denied/);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("rejects protected names", async () => {
  const { parent, root } = await fixture();
  try {
    for (const candidate of [".git/config", ".env", ".env.local", "id_rsa", "config/private.pem", ".hermes/events.jsonl"]) {
      await assert.rejects(resolveWritable(root, candidate), /protected_path/);
    }
    assert.equal(isProtectedRelativePath("src/app.py"), false);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("rejects existing symlink components", async (t) => {
  const { parent, root, outside } = await fixture();
  try {
    const link = path.join(root, "linked");
    try {
      await symlink(outside, link, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES"].includes(error?.code)) {
        t.skip("symlink creation unavailable");
        return;
      }
      throw error;
    }
    await assert.rejects(resolveReadable(root, "linked/secret.txt"), /workspace_symlink_denied/);
    await assert.rejects(resolveWritable(root, "linked/new.txt"), /workspace_symlink_denied/);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("rejects high-confidence credential content without echoing it", () => {
  const samples = [
    ["-----BEGIN ", "PRIVATE KEY-----", "\nnot-real\n"].join(""),
    `token=ghp_${"A".repeat(40)}`,
    `api=sk-${"B".repeat(30)}`,
    `jwt=eyJ${"a".repeat(20)}.${"b".repeat(20)}.${"c".repeat(20)}`,
  ];
  for (const sample of samples) {
    assert.throws(() => assertSafeContent(sample), (error) => {
      assert.equal(error.message, "credential_like_content");
      assert.equal(error.message.includes(sample), false);
      return true;
    });
  }
  assert.equal(assertSafeContent("VALUE = 1\n"), "VALUE = 1\n");
});
