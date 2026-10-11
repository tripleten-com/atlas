// The one place the frontend talks to the Atlas API.
//
// Every request goes through `request()`, which adds the base URL and the bearer token and
// turns failures into an ApiError. Components never build URLs themselves: they call one of
// the named functions at the bottom of this file. To reach a new endpoint, add a function here.

// Where the API lives, first match wins:
//   1. window.__ATLAS_CONFIG__.apiUrl   written into config.js when the production container starts
//   2. import.meta.env.VITE_API_URL     the Vite dev server's environment (docker-compose.yml)
//   3. ""                               same origin: the page's own host serves /api (the Ingress)
const RUNTIME_API_URL = (typeof window !== "undefined" && window.__ATLAS_CONFIG__?.apiUrl) || "";
const API_URL = (RUNTIME_API_URL || import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const TOKEN_KEY = "atlas.token";

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

// ---------------------------------------------------------------- the session token

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// Fired when the API says the session is no longer valid, so the app can send the user to login.
export const SESSION_EXPIRED_EVENT = "atlas:session-expired";

// ---------------------------------------------------------------- the helpers

function buildUrl(path, params) {
  // the second argument makes a relative API_URL ("" = same origin) resolve against the page
  const url = new URL(API_URL + path, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  return url.toString();
}

async function request(method, path, { params, body, signal } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the Atlas API. Is the backend running?");
  }

  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) {
    if (response.status === 401 && token) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : response.statusText;
    throw new ApiError(response.status, message || `Request failed (${response.status})`, data);
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

export function get(path, params, options) {
  return request("GET", path, { params, ...options });
}

export function post(path, body, options) {
  return request("POST", path, { body, ...options });
}

// Read a Server-Sent Events stream with fetch(), so the Authorization header can be sent.
// Calls onEvent({ event, data }) for every message. Returns a function that closes the stream.
export function stream(path, params, onEvent, { onOpen, onError } = {}) {
  const controller = new AbortController();
  (async () => {
    try {
      const response = await fetch(buildUrl(path, params), {
        headers: { Authorization: `Bearer ${getToken()}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new ApiError(response.status, "Live updates unavailable");
      onOpen?.();
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop();
        for (const part of parts) {
          let event = "message";
          const dataLines = [];
          for (const line of part.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue; // keep-alive comment
          onEvent({ event, data: safeJson(dataLines.join("\n")) });
        }
      }
      if (!controller.signal.aborted) onError?.(new Error("stream ended"));
    } catch (err) {
      if (err.name !== "AbortError") onError?.(err);
    }
  })();
  return () => controller.abort();
}

// ---------------------------------------------------------------- auth

export const login = (email, password) => post("/api/auth/login", { email, password });
export const getMe = () => get("/api/auth/me");

// ---------------------------------------------------------------- facilities and reference data

export const getFacilities = () => get("/api/facilities");
export const getReference = () => get("/api/reference");
export const getOrg = () => get("/api/org");

// ---------------------------------------------------------------- patients

export const getPatients = (params, options) => get("/api/patients", params, options);
export const getPatient = (id) => get(`/api/patients/${id}`);
export const getVitals = (id, params) => get(`/api/patients/${id}/vitals`, params);
export const getLabs = (id, params) => get(`/api/patients/${id}/labs`, params);
export const getMedications = (id, params) => get(`/api/patients/${id}/medications`, params);
export const getPatientEncounters = (id) => get(`/api/patients/${id}/encounters`);
export const getTimeline = (id, params) => get(`/api/patients/${id}/timeline`, params);

// ---------------------------------------------------------------- caseload

export const getEncounters = (params, options) => get("/api/encounters", params, options);
export const getEncounter = (id) => get(`/api/encounters/${id}`);
export const getResults = (params, options) => get("/api/results", params, options);
export const getDashboardSummary = (params) => get("/api/dashboard/summary", params);
export const getDashboardDaily = (params) => get("/api/dashboard/daily", params);
export const getDashboardBreakdown = (params) => get("/api/dashboard/breakdown", params);

// ---------------------------------------------------------------- live data

export const getActivity = (params) => get("/api/activity", params);
export const streamActivity = (params, onEvent, handlers) => stream("/api/activity/stream", params, onEvent, handlers);

// ---------------------------------------------------------------- integrations

export const getIntegrations = () => get("/api/integrations");
export const getIntegration = (id) => get(`/api/integrations/${id}`);
export const getIntegrationEvents = (id, params) => get(`/api/integrations/${id}/events`, params);
export const getIntegrationVolume = (id, params) => get(`/api/integrations/${id}/volume`, params);
export const resyncIntegration = (id) => post(`/api/integrations/${id}/resync`);
export const pauseIntegration = (id) => post(`/api/integrations/${id}/pause`);
export const resumeIntegration = (id) => post(`/api/integrations/${id}/resume`);
export const retryFailedEvents = (id) => post(`/api/integrations/${id}/retry-failed`);

// ---------------------------------------------------------------- admin

export const getUsage = (params) => get("/api/usage", params);
export const getAudit = (params) => get("/api/audit", params);

export { API_URL };

// ---------------------------------------------------------------- AI features

export const getReadmissionRisk = (id) => get(`/api/patients/${id}/risk/readmission`);
export const postStaySummary = (id) => post(`/api/patients/${id}/summary`);
