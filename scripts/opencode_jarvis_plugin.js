// Jarvis — PROVENANCE plugin for opencode (twin of Claude Code's PostToolUse
// hook). It reports every edit (edit/write) to the swarm: POST to
// /api/swarm/op with {terminal_id, tool_name, tool_input}, the SAME shape the
// Claude hook sends — so opencode stops being a ghost for coordination
// (ownership, collisions, jv estado, commit per hunk).
//
// PASSIVE observer: it does NOT change the model's tool set and NEVER throws —
// a throw in tool.execute.* blocks the agent's tool. Everything is wrapped in
// try/catch and the fetch is fire-and-forget with timeout: if Jarvis is down or
// slow, the agent keeps going anyway.
//
// Jarvis installs it at boot (plotspace/core/cli_adapters.py) in
// ~/.config/opencode/plugin/ — a single file covers ALL opencode agents
// launched in tmux. Identity comes from JARVIS_TERMINAL_ID, which Jarvis injects
// into each pane's environment (terminals.py); without that var (opencode
// outside Jarvis) the plugin is a no-op.
import { readFile } from "node:fs/promises";

const TID = process.env.JARVIS_TERMINAL_ID;
const PORT = process.env.JARVIS_PORT || "3000";
// Returns the server response (or null). The caller decides whether to wait:
// to record the edit it's not needed, but the BRIEFING travels in that response
// and opencode has no prompt hook — this is its only channel to find out who
// else is working in the tree.
async function post(tool_name, tool_input, cwd) {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/api/swarm/op`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ terminal_id: Number(TID), tool_name, tool_input, cwd, source: "opencode" }),
      signal: AbortSignal.timeout(1500),
    });
    return await r.json();
  } catch { return null; }       // Jarvis down/slow: the agent keeps going anyway
}

// Text the agent has to read: surface collision (it deleted something another
// one uses) and/or swarm briefing. The server already dedupes the briefing by
// signature, so this doesn't repeat the same thing on every edit.
function textoParaElAgente(r) {
  if (!r || typeof r !== "object") return "";
  return [r.aviso_texto, r.briefing].filter(Boolean).map(String).join("\n\n");
}

// Attaches it to the tool output, which is what the model reads. Defensive on
// purpose: if this opencode version doesn't expose `output` as a string,
// nothing is touched — never break the agent over a notice.
function inyectar(salida, texto) {
  try {
    if (!texto || !salida || typeof salida !== "object") return;
    if (typeof salida.output === "string") salida.output += `\n\n${texto}`;
    else if (typeof salida.title === "string") salida.metadata = {
      ...(salida.metadata || {}), jarvis: texto };
  } catch { /* never break */ }
}

export const JarvisSwarm = async ({ directory, worktree }) => {
  if (!TID) return {};                       // opencode outside Jarvis → no-op
  const cwd = worktree || directory || "";
  const before = new Map();                  // callID → old content (for write)
  return {
    // The "before" of a write (full overwrite) doesn't come in the args: it's
    // read from disk BEFORE it's overwritten. For edit it's not needed (it
    // brings old+new).
    "tool.execute.before": async ({ tool, callID }, { args }) => {
      try {
        if (tool === "write" && args && args.filePath) {
          before.set(callID, await readFile(args.filePath, "utf8").catch(() => ""));
        }
      } catch { /* observer: never break the agent */ }
    },
    // Report after a SUCCESSFUL edit (this hook doesn't run if the tool failed).
    // `salida` is the tool result: the collision notice and the swarm briefing
    // are attached there — opencode has no prompt hook, so this is its only
    // context input channel.
    "tool.execute.after": async ({ tool, callID, args }, salida) => {
      try {
        if (!args) return;
        let r = null;
        if (tool === "edit") {
          r = await post("edit", { filePath: args.filePath, oldString: args.oldString,
                                   newString: args.newString }, cwd);
        } else if (tool === "write") {
          r = await post("write", { filePath: args.filePath,
                                    oldString: before.get(callID) ?? "",
                                    content: args.content }, cwd);
          before.delete(callID);
        }
        // `patch` (multi-file) stays out of the pilot: its args don't bring a
        // clean path. edit + write cover 99%.
        inyectar(salida, textoParaElAgente(r));
      } catch { /* never break */ }
    },
  };
};
