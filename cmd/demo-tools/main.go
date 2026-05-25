// Package main — MCP tool server for the multi-agent demo. Same binary,
// different tool surface per --role flag. Roles:
//
//	orchestrator-fs       read-only filesystem (read_file, list_directory)
//	orchestrator-delegate delegate_to tool (HTTP-POSTs to a named worker)
//	worker-delegate       same delegate_to tool but with orchestrator added
//	                      as a valid target (so workers can loop back to
//	                      drive the circular_delegation scenario)
//	data-worker           DB-shaped tools (query_database, fetch_orders,
//	                      create_order)
//	notification-worker   send_notification, email_customer
//
// The delegate_to tool's input schema declares the exact arg keys the
// gateway's interceptor reads from action.Parameters (delegation_chain_id /
// delegator_agent_id / etc.). The HTTP forward to the target worker
// inherits chain_id so backend ingest accumulates real multi-hop chains.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type request struct {
	JSONRPC string          `json:"jsonrpc"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
	ID      json.RawMessage `json:"id"`
}

type response struct {
	JSONRPC string          `json:"jsonrpc"`
	Result  interface{}     `json:"result"`
	ID      json.RawMessage `json:"id"`
}

type errorResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	Error   interface{}     `json:"error"`
	ID      json.RawMessage `json:"id"`
}

type tool struct {
	Name        string      `json:"name"`
	Description string      `json:"description"`
	InputSchema interface{} `json:"inputSchema"`
}

var (
	role      string
	workspace string
)

func main() {
	flag.StringVar(&role, "role", "", "Tool surface: orchestrator-fs|orchestrator-delegate|worker-delegate|data-worker|notification-worker")
	flag.StringVar(&workspace, "workspace", "/workspace", "Workspace root (for fs tools)")
	flag.Parse()

	if role == "" {
		fmt.Fprintln(os.Stderr, "--role is required")
		os.Exit(2)
	}

	scanner := bufio.NewScanner(os.Stdin)
	buf := make([]byte, 64*1024)
	scanner.Buffer(buf, 4*1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var req request
		if err := json.Unmarshal(line, &req); err != nil {
			writeJSON(errorResponse{JSONRPC: "2.0", Error: map[string]any{"code": -32700, "message": "parse error"}})
			continue
		}
		writeJSON(handle(&req))
	}
}

func handle(req *request) interface{} {
	switch req.Method {
	case "initialize":
		return response{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
			"protocolVersion": "2024-11-05",
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "demo-tools", "version": "0.1.0"},
		}}
	case "tools/list":
		return response{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"tools": toolsForRole()}}
	case "tools/call":
		return handleCall(req)
	default:
		return response{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{"status": "ok"}}
	}
}

// ── Tool lists per role ────────────────────────────────────────────────────

func toolsForRole() []tool {
	switch role {
	case "orchestrator-fs":
		return []tool{
			{
				Name:        "read_file",
				Description: "Read a file from the orchestrator's workspace",
				InputSchema: map[string]any{
					"type":       "object",
					"properties": map[string]any{"path": map[string]any{"type": "string"}},
					"required":   []string{"path"},
				},
			},
			{
				Name:        "list_directory",
				Description: "List a directory",
				InputSchema: map[string]any{
					"type":       "object",
					"properties": map[string]any{"path": map[string]any{"type": "string"}},
					"required":   []string{"path"},
				},
			},
		}

	case "orchestrator-delegate", "worker-delegate":
		// Same schema for both roles — what changes is which target_agent
		// names the runner is told to use in prompts. The gateway reads
		// these args as the delegation hop metadata.
		return []tool{
			{
				Name:        "delegate_to",
				Description: "Delegate a task to another agent. Posts the task to the target worker over HTTP and returns its response. Used to compose multi-agent workflows.",
				InputSchema: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"delegation_chain_id":     map[string]any{"type": "string", "description": "Chain identifier — use the same value across related hops"},
						"delegator_agent_id":      map[string]any{"type": "string", "description": "The current agent's MITRITY agent ID (env: MITRITY_AGENT_ID)"},
						"delegator_agent_name":    map[string]any{"type": "string"},
						"delegator_mission_scope": map[string]any{"type": "string"},
						"to_agent_id":             map[string]any{"type": "string", "description": "Target agent's MITRITY agent ID"},
						"to_agent_name":           map[string]any{"type": "string"},
						"target_worker":           map[string]any{"type": "string", "enum": []string{"orchestrator", "data-worker", "notification-worker"}, "description": "Which worker to dispatch to over HTTP"},
						"task":                    map[string]any{"type": "string", "description": "What to delegate (free text)"},
					},
					"required": []string{"delegation_chain_id", "delegator_agent_id", "to_agent_id", "target_worker", "task"},
				},
			},
		}

	case "data-worker":
		return []tool{
			{
				Name:        "query_database",
				Description: "Read-only SELECT against the customer orders database",
				InputSchema: map[string]any{
					"type":       "object",
					"properties": map[string]any{"query": map[string]any{"type": "string"}},
					"required":   []string{"query"},
				},
			},
			{
				Name:        "fetch_orders",
				Description: "Fetch orders for a given customer",
				InputSchema: map[string]any{
					"type":       "object",
					"properties": map[string]any{"customer_id": map[string]any{"type": "string"}},
					"required":   []string{"customer_id"},
				},
			},
			{
				Name:        "create_order",
				Description: "Create a new order. WRITE tool — orchestrators without write perms get blocked by privilege_escalation when they delegate this.",
				InputSchema: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"customer_id": map[string]any{"type": "string"},
						"items":       map[string]any{"type": "string"},
					},
					"required": []string{"customer_id", "items"},
				},
			},
		}

	case "notification-worker":
		return []tool{
			{
				Name:        "send_notification",
				Description: "Send a notification to a channel (Slack-like)",
				InputSchema: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"channel": map[string]any{"type": "string"},
						"message": map[string]any{"type": "string"},
					},
					"required": []string{"channel", "message"},
				},
			},
			{
				Name:        "email_customer",
				Description: "Email a customer with a subject + body",
				InputSchema: map[string]any{
					"type": "object",
					"properties": map[string]any{
						"customer_id": map[string]any{"type": "string"},
						"subject":     map[string]any{"type": "string"},
						"body":        map[string]any{"type": "string"},
					},
					"required": []string{"customer_id", "subject", "body"},
				},
			},
		}
	}
	return nil
}

// ── Tool call dispatch ─────────────────────────────────────────────────────

func handleCall(req *request) interface{} {
	var p struct {
		Name      string         `json:"name"`
		Arguments map[string]any `json:"arguments"`
	}
	if req.Params != nil {
		_ = json.Unmarshal(req.Params, &p)
	}

	result, err := dispatch(p.Name, p.Arguments)
	if err != nil {
		return errorResponse{JSONRPC: "2.0", ID: req.ID,
			Error: map[string]any{"code": -32000, "message": err.Error()}}
	}
	return response{JSONRPC: "2.0", ID: req.ID, Result: map[string]any{
		"content": []map[string]any{{"type": "text", "text": result}},
	}}
}

func dispatch(name string, args map[string]any) (string, error) {
	switch name {
	case "read_file", "list_directory":
		return handleFS(name, args)
	case "delegate_to":
		return handleDelegate(args)
	case "query_database", "fetch_orders", "create_order":
		return handleData(name, args)
	case "send_notification", "email_customer":
		return handleNotify(name, args)
	default:
		return "", fmt.Errorf("unknown tool: %s", name)
	}
}

// ── FS (orchestrator only) ─────────────────────────────────────────────────
//
// Intentionally permissive: no workspace-jail check, no traversal sanitization.
// The MITRITY gateway in front of demo-tools is the security boundary — it's
// what the demo demonstrates. If we hardened the upstream tool, the gateway's
// blocking of "/etc/passwd" reads etc. would become invisible. Production
// upstream MCP servers should defense-in-depth, but demo upstreams stay raw
// so the gateway's enforcement is the visible event.

func handleFS(name string, args map[string]any) (string, error) {
	path, _ := args["path"].(string)
	if path == "" {
		return "", fmt.Errorf("path is required")
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(workspace, path)
	}
	path = filepath.Clean(path)
	switch name {
	case "read_file":
		data, err := os.ReadFile(path)
		if err != nil {
			return "", fmt.Errorf("read %s: %w", path, err)
		}
		return string(data), nil
	case "list_directory":
		entries, err := os.ReadDir(path)
		if err != nil {
			return "", fmt.Errorf("list %s: %w", path, err)
		}
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			names = append(names, e.Name())
		}
		return strings.Join(names, "\n"), nil
	}
	return "", fmt.Errorf("unknown fs tool: %s", name)
}

// ── Delegate (orchestrator + workers) ──────────────────────────────────────
//
// The gateway has ALREADY intercepted and evaluated the call by the time we
// run — so by the time this code executes, the hop's been governed and
// allowed/alerted (if blocked, we never run). Our job is to forward the task
// over HTTP to the target worker, passing the chain metadata so the
// downstream gateway can record the next hop on the same chain.

func handleDelegate(args map[string]any) (string, error) {
	target, _ := args["target_worker"].(string)
	chainID, _ := args["delegation_chain_id"].(string)
	delegator, _ := args["delegator_agent_id"].(string)
	task, _ := args["task"].(string)

	if target == "" {
		return "", fmt.Errorf("target_worker is required")
	}

	urlVar := map[string]string{
		"orchestrator":        "ORCHESTRATOR_URL",
		"data-worker":         "DATA_WORKER_URL",
		"notification-worker": "NOTIFY_WORKER_URL",
	}[target]
	url := os.Getenv(urlVar)
	if url == "" {
		return "", fmt.Errorf("env var %s is unset — cannot reach %s", urlVar, target)
	}

	body, _ := json.Marshal(map[string]string{
		"chain_id":           chainID,
		"delegator_agent_id": delegator,
		"task":               task,
	})
	httpReq, err := http.NewRequest(http.MethodPost, url+"/task", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(httpReq)
	if err != nil {
		return "", fmt.Errorf("POST %s/task: %w", url, err)
	}
	defer func() { _ = resp.Body.Close() }()
	respBody, _ := io.ReadAll(resp.Body)

	if resp.StatusCode >= 400 {
		return "", fmt.Errorf("%s returned %d: %s", target, resp.StatusCode, string(respBody))
	}
	return fmt.Sprintf("[delegated to %s, chain=%s]\n%s", target, chainID, string(respBody)), nil
}

// ── Data (data-worker only) — mock responses ──────────────────────────────

func handleData(name string, args map[string]any) (string, error) {
	switch name {
	case "query_database":
		q, _ := args["query"].(string)
		return fmt.Sprintf("[MOCK] %s\nresult: 3 rows", q), nil
	case "fetch_orders":
		cid, _ := args["customer_id"].(string)
		return fmt.Sprintf("[MOCK] orders for %s: [\"ord-1\", \"ord-2\"]", cid), nil
	case "create_order":
		cid, _ := args["customer_id"].(string)
		items, _ := args["items"].(string)
		return fmt.Sprintf("[MOCK] created order for %s with items: %s", cid, items), nil
	}
	return "", fmt.Errorf("unknown data tool: %s", name)
}

// ── Notify (notification-worker only) — mock responses ────────────────────

func handleNotify(name string, args map[string]any) (string, error) {
	switch name {
	case "send_notification":
		ch, _ := args["channel"].(string)
		msg, _ := args["message"].(string)
		return fmt.Sprintf("[MOCK] sent to %s: %s", ch, msg), nil
	case "email_customer":
		cid, _ := args["customer_id"].(string)
		subj, _ := args["subject"].(string)
		return fmt.Sprintf("[MOCK] emailed %s: %s", cid, subj), nil
	}
	return "", fmt.Errorf("unknown notify tool: %s", name)
}

// ── stdio JSON-RPC helper ──────────────────────────────────────────────────

func writeJSON(v interface{}) {
	b, _ := json.Marshal(v)
	_, _ = os.Stdout.Write(append(b, '\n'))
}
