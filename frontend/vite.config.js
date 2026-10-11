import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server runs on port 3000 (the port the course materials use).
// Inside Docker on Windows and macOS, file events do not cross the bind mount, so poll.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    watch: process.env.CHOKIDAR_USEPOLLING ? { usePolling: true, interval: 300 } : undefined,
  },
  preview: { port: 3000 },
});
