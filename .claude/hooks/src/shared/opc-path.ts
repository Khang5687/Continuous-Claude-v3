/**
 * Cross-platform OPC directory resolution for hooks.
 *
 * Supports running Claude Code in any directory by:
 * 1. Checking CLAUDE_OPC_DIR environment variable (explicit override)
 * 2. Reading ~/.claude/opc_dir (written by installer; supports global hooks)
 * 3. Falling back to ${CLAUDE_PROJECT_DIR}/opc (local checkout)
 * 4. Gracefully degrading if neither exists
 */

import { existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import { homedir } from 'os';

/**
 * Get the OPC directory path, or null if not available.
 *
 * Resolution order:
 * 1. CLAUDE_OPC_DIR env var (explicit override)
 * 2. ~/.claude/opc_dir (installer-persisted path to opc/)
 * 3. ${CLAUDE_PROJECT_DIR}/opc (for running within an OPC checkout)
 *
 * @returns Path to opc directory, or null if not found
 */
export function getOpcDir(): string | null {
  // 1. Try env var (works when hooks are installed globally)
  const envOpcDir = process.env.CLAUDE_OPC_DIR;
  if (envOpcDir && existsSync(envOpcDir)) {
    return envOpcDir;
  }

  // 2. Try persisted opc/ location written by the installer (~/.claude/opc_dir)
  const homeDir = process.env.HOME || process.env.USERPROFILE || homedir();
  if (homeDir) {
    const globalClaude = join(homeDir, '.claude');
    const opcDirFile = join(globalClaude, 'opc_dir');

    if (existsSync(opcDirFile)) {
      try {
        const raw = readFileSync(opcDirFile, 'utf-8').trim();
        const cleaned = raw.replace(/^['"]|['"]$/g, '');
        if (cleaned) {
          const candidate = resolve(cleaned);
          if (
            existsSync(candidate) &&
            existsSync(join(candidate, 'pyproject.toml')) &&
            existsSync(join(candidate, 'scripts'))
          ) {
            return candidate;
          }
        }
      } catch {
        // Ignore unreadable/malformed opc_dir
      }
    }
  }

  // 3. Try project-relative path
  const projectDir = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const localOpc = join(projectDir, 'opc');
  if (existsSync(localOpc)) {
    return localOpc;
  }

  // 4. Try global ~/.claude only if it is itself a uv project (pyproject.toml present).
  // Without a pyproject, running `uv run` from ~/.claude will not have dependencies.
  if (homeDir) {
    const globalClaude = join(homeDir, '.claude');
    const globalScripts = join(globalClaude, 'scripts', 'core');
    const globalPyproject = join(globalClaude, 'pyproject.toml');
    if (existsSync(globalPyproject) && existsSync(globalScripts)) {
      return globalClaude;
    }
  }

  // 5. Not available
  return null;
}

/**
 * Get OPC directory or exit gracefully if not available.
 *
 * Use this in hooks that require OPC infrastructure.
 * If OPC is not available, outputs {"result": "continue"} and exits,
 * allowing the hook to be a no-op in non-CC projects.
 *
 * @returns Path to opc directory (never null - exits if not found)
 */
export function requireOpcDir(): string {
  const opcDir = getOpcDir();
  if (!opcDir) {
    // Graceful degradation - hook becomes no-op
    console.log(JSON.stringify({ result: "continue" }));
    process.exit(0);
  }
  return opcDir;
}

/**
 * Check if OPC infrastructure is available.
 *
 * Use this for optional OPC features that should silently skip
 * when running outside a Continuous-Claude environment.
 *
 * @returns true if OPC directory exists and is accessible
 */
export function hasOpcDir(): boolean {
  return getOpcDir() !== null;
}
