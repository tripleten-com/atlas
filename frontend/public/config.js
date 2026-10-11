// Runtime settings for the Atlas frontend. Loaded by index.html before the app itself.
//
// This default copy ships with the source and is what the Vite dev server serves. The
// production image (Dockerfile.prod) overwrites it at container start from VITE_API_URL,
// so one built image can point at different API addresses. An empty apiUrl means
// "same origin": the browser calls /api/... on the host that served the page.
window.__ATLAS_CONFIG__ = { apiUrl: "" };
