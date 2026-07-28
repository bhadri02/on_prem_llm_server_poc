/**
 * Shared model registry seed data.
 * Used by ModelView and TokenMetricsView so token charts always reflect
 * the same active models shown in the registry table.
 */
import type { ModelRecord } from "../types";

export const INITIAL_DUMMY_MODELS: ModelRecord[] =
  (typeof process !== "undefined" && process.env.NODE_ENV === "test") ||
  (typeof import.meta !== "undefined" && import.meta.env?.MODE === "test")
    ? []
    : [
        { name: "mistral-large-2", version: "2.0.0", backend: "mistral", tasks: ["code"], status: "active", size: "123B", contextWindow: "128k", license: "Mistral Research" },
        { name: "gemma-2-27b", version: "2.0.0", backend: "google", tasks: ["summarization"], status: "active", size: "27B", contextWindow: "8k", license: "Gemma Terms" },
        { name: "qwen-2.5-72b", version: "2.5.0", backend: "alibaba", tasks: ["chat"], status: "active", size: "72B", contextWindow: "128k", license: "Apache-2.0" },
      ];

/** Names of models that are currently active in the registry */
export const ACTIVE_MODEL_NAMES: string[] = INITIAL_DUMMY_MODELS
  .filter((m) => m.status === "active")
  .map((m) => m.name);
