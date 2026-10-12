import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development, /api is proxied to the FastAPI server so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
