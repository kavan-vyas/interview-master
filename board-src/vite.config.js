import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Built into ../static/tl so FastAPI serves it offline (eduroam-proof).
export default defineConfig({ plugins: [react()], base: "/static/tl/", build: { outDir: "../static/tl", emptyOutDir: true } });
