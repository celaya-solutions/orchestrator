<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/1fcfoNhCXRTiOka7Sfa28wZqHZhF-Uxw1

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. (Optional) Set `VITE_VISUALIZER_API_BASE` if your backend is not on `http://localhost:8081`
3. Run the app:
   - Frontend only (expects backend already running on port 8081): `npm run dev`
   - Frontend + backend together: `npm run dev:all` (starts `python -m ralph_orchestrator.web.actions_server` from the repo root and Vite)
