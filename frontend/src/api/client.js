// Thin fetch wrapper: cookie session + CSRF header on mutations, JSON errors surfaced as ApiError.
export class ApiError extends Error {
  status;
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
let csrfToken = sessionStorageGet("rd_csrf");
function sessionStorageGet(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
export function setCsrf(token) {
  csrfToken = token;
  try {
    if (token) window.sessionStorage.setItem("rd_csrf", token);
    else window.sessionStorage.removeItem("rd_csrf");
  } catch {
    /* storage unavailable: keep in memory */
  }
}
async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : text;
    throw new ApiError(response.status, formatDetail(detail) || `Request failed (${response.status})`);
  }
  return data;
}
function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "object" && d && "msg" in d ? String(d.msg) : String(d))).join("; ");
  }
  return detail ? JSON.stringify(detail) : "";
}
export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
};
