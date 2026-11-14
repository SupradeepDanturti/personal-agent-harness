/** HTTP boundary between the UI and the independently running Python API. */
export type Item = Record<string, unknown>;
export type Api = (
  path: string,
  body?: unknown,
  method?: string,
) => Promise<Item>;

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export function createApi(token: string, onUnauthorized?: () => void): Api {
  return async (path, body, method = body === undefined ? "GET" : "POST") => {
    const response = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (response.status === 401) {
      onUnauthorized?.();
      throw new ApiError("Workspace token was rejected or changed. Reconnect your workspace.", 401);
    }
    const raw = await response.text();
    let data: Item;
    try {
      const parsed: unknown = raw ? JSON.parse(raw) : {};
      data =
        parsed && typeof parsed === "object" && !Array.isArray(parsed)
          ? (parsed as Item)
          : {};
    } catch {
      throw new ApiError("The server returned an unreadable response.", response.status);
    }
    if (!response.ok) {
      throw new ApiError(
        typeof data.error === "string"
          ? data.error
          : `Request failed (${response.status}).`, response.status,
      );
    }
    return data;
  };
}
