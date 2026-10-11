let token = "";

export const setToken = (t: string) => {
  token = t;
};

/** Raised when the API refuses an action. `enforced` is true when a database trigger or privilege said no. */
export class ApiError extends Error {
  constructor(message: string, public enforced: boolean) {
    super(message);
  }
}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch(() => { throw new ApiError("Unable to reach the validation service. Check your connection and try again.", false); });
  if (!res.ok) {
    if ([502, 503, 504].includes(res.status)) throw new ApiError("The validation service is unavailable. Check that the backend is running, then try again.", false);
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail), err.enforced_by === "database");
  }
  return res.json();
}

export const fmt = (iso?: string | null) => (iso ? new Date(iso).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "");
