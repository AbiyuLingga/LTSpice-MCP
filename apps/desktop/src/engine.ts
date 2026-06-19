import { invoke } from "@tauri-apps/api/core";

export type EngineProject = {
  displayName: string;
  projectDir: string;
  projectId: string;
  revision: number;
  schemaVersion: string;
};

type RpcError = {
  code: number;
  data?: { code?: string; details?: Record<string, unknown> };
  message: string;
};

type RpcResponse<T> =
  | { id: number; jsonrpc: "2.0"; result: T }
  | { error: RpcError; id: number | null; jsonrpc: "2.0" };

export type EngineBridge = {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
};

let nextRequestId = 1;

export const desktopBridge: EngineBridge = {
  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const response = await invoke<RpcResponse<T>>("engine_request", {
      request: {
        id: nextRequestId++,
        jsonrpc: "2.0",
        method,
        params,
      },
    });
    if ("error" in response) {
      throw new Error(response.error.message);
    }
    return response.result;
  },
};
